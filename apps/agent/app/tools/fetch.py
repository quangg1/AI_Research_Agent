from __future__ import annotations

import hashlib
import html
import io
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.domain.citations import looks_like_nav_chrome
from app.domain.schema import ALLOWED_DOC_HOSTS, AgentName
from app.domain.credibility import credibility_score, host_of
from app.observability.logging import logger

TAG_RE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", re.I)
SPACE_RE = re.compile(r"\s+")
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARS = 40_000
MAX_PDF_PAGES = 40


def is_allowed(url: str) -> bool:
    host = host_of(url)
    if not host:
        return False
    if host in ALLOWED_DOC_HOSTS:
        return True
    return any(host.endswith(f".{h}") or host == h for h in ALLOWED_DOC_HOSTS)


def strip_html(raw: str) -> str:
    """Extract readable full-page text while dropping navigation and scripts."""
    soup = BeautifulSoup(raw or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text(" ", strip=True)
    text = html.unescape(text)
    return SPACE_RE.sub(" ", text).strip()


def extract_content(raw: bytes, content_type: str = "") -> str:
    if not raw:
        return ""
    is_pdf = "pdf" in content_type.lower() or raw[:5] == b"%PDF-"
    if is_pdf:
        try:
            reader = PdfReader(io.BytesIO(raw))
            pages: list[str] = []
            chars = 0
            for index, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
                text = page.extract_text() or ""
                pages.append(f"[[page {index}]] {text}")
                chars += len(text)
                if chars >= MAX_EXTRACTED_CHARS:
                    break
            return SPACE_RE.sub(" ", " ".join(pages)).strip()[:MAX_EXTRACTED_CHARS]
        except Exception as exc:
            logger.warning("pdf_extract_failed %s", exc)
            return ""
    encoding = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    if match:
        encoding = match.group(1)
    return strip_html(raw.decode(encoding, errors="replace"))[:MAX_EXTRACTED_CHARS]


def fetch_url(url: str, timeout: float = 12.0) -> str:
    if not is_allowed(url):
        return ""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "KilnResearch/0.1"}) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                declared = int(response.headers.get("content-length") or 0)
                if declared > MAX_DOWNLOAD_BYTES:
                    logger.warning("fetch_too_large %s %s", url, declared)
                    return ""
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        logger.warning("fetch_too_large_stream %s", url)
                        return ""
                    chunks.append(chunk)
                return extract_content(b"".join(chunks), response.headers.get("content-type", ""))
    except Exception as exc:
        logger.warning("fetch_failed %s %s", url, exc)
        return ""


def evidence_from_url(url: str, title: str = "", body: str = "") -> dict | None:
    text = body or fetch_url(url)
    if len(text) < 80 or looks_like_nav_chrome(text[:800]):
        return None
    tier, score = credibility_score(url)
    eid = "ev_" + hashlib.sha1(url.encode()).hexdigest()[:10]
    return {
        "id": eid,
        "title": title or urlparse(url).path.rsplit("/", 1)[-1] or url,
        "url": url,
        "snippet": text[:1600],
        "quote": text[:500],
        "source_agent": AgentName.DOCS.value,
        "tier": tier.value,
        "credibility": score,
        "published": "",
        "full_text": text[:MAX_EXTRACTED_CHARS],
    }
