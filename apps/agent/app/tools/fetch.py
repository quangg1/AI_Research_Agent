from __future__ import annotations

import hashlib
import html
import io
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

import ipaddress

from app.domain.citations import is_citable_url, looks_like_nav_chrome
from app.domain.schema import AgentName
from app.domain.credibility import credibility_score, host_of
from app.observability.logging import logger

TAG_RE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", re.I)
SPACE_RE = re.compile(r"\s+")
TABLE_ROW_RE = re.compile(r"^(\s*\S+(?:\s{2,}|\t|\|)\S+.*){2,}$")
# Zero-width / invisible characters used to split injection keywords apart
# (e.g. "Ignore<ZWSP>all<ZWSP>previous...") so literal pattern matching
# below still catches them. Same set app/domain/injection_guard.py strips.
_ZERO_WIDTH_RE = re.compile(
    r"[​-‏‪-‮⁠-⁤﻿­͏؜ᅟᅠ឴឵᠎ㅤﾠ]"
)
INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.I),
    re.compile(r"disregard\s+(the\s+)?(above|system)\s+", re.I),
    re.compile(r"<\s*/?\s*system\s*>", re.I),
    # Fake role-header lines (a classic chat-injection vector) — not just
    # "system:", any of the turn roles a chat prompt template recognizes.
    re.compile(r"^\s*(?:system|assistant|user|human)\s*:\s*", re.I | re.M),
    re.compile(r"you\s+are\s+now\s+(?:a|an)\s+", re.I),
    re.compile(r"developer\s+message\s*:", re.I),
)
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARS = 40_000
MAX_PDF_PAGES = 40


BLOCKED_FETCH_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
}
BLOCKED_FETCH_SUFFIXES = (".local", ".internal")


def is_fetchable(url: str) -> bool:
    """True for specific citable http(s) pages, excluding private/blocked hosts."""
    if not is_citable_url(url):
        return False
    host = host_of(url)
    if not host:
        return False
    if host in BLOCKED_FETCH_HOSTS:
        return False
    if any(host.endswith(suffix) for suffix in BLOCKED_FETCH_SUFFIXES):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        pass
    return True


def is_allowed(url: str) -> bool:
    """Backward-compatible alias — any citable public URL may be fetched."""
    return is_fetchable(url)


def strip_html(raw: str) -> str:
    """Extract readable full-page text while dropping navigation and scripts."""
    soup = BeautifulSoup(raw or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text(" ", strip=True)
    text = html.unescape(text)
    return SPACE_RE.sub(" ", text).strip()


def sanitize_fetched_content(text: str) -> str:
    """Strip common prompt-injection patterns from untrusted page text."""
    if not text:
        return ""
    cleaned: list[str] = []
    for line in text.splitlines():
        # Zero-width chars can split an injection phrase's words apart
        # ("Ignore<ZWSP>all<ZWSP>previous...") to dodge literal matching —
        # scan a stripped copy but keep the original line when it's clean.
        scan_line = _ZERO_WIDTH_RE.sub(" ", line)
        if any(pattern.search(scan_line) for pattern in INJECTION_PATTERNS):
            cleaned.append("[filtered untrusted instruction]")
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _annotate_table_rows(text: str) -> str:
    rows: list[str] = []
    for line in text.splitlines():
        if TABLE_ROW_RE.match(line.strip()):
            rows.append(f"[table] {line.strip()}")
        else:
            rows.append(line)
    return "\n".join(rows)


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
            body = SPACE_RE.sub(" ", " ".join(pages)).strip()[:MAX_EXTRACTED_CHARS]
            return sanitize_fetched_content(_annotate_table_rows(body))
        except Exception as exc:
            logger.warning("pdf_extract_failed %s", exc)
            return ""
    encoding = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    if match:
        encoding = match.group(1)
    return sanitize_fetched_content(strip_html(raw.decode(encoding, errors="replace"))[:MAX_EXTRACTED_CHARS])


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
    
    # Chunk the text to avoid title pages becoming the quote
    # Take a middle chunk that's more likely to have substance
    tier, score = credibility_score(url)
    eid = "ev_" + hashlib.sha1(url.encode()).hexdigest()[:10]
    
    # Split into rough chunks and skip obvious title pages
    chunks = _chunk_text_for_evidence(text)
    
    # Select best chunk for quote (prefer substantive content over title page)
    quote_chunk = _select_best_quote_chunk(chunks)
    snippet_text = text[:1600] if len(chunks) <= 1 else chunks[0][:1600]
    
    return {
        "id": eid,
        "title": title or urlparse(url).path.rsplit("/", 1)[-1] or url,
        "url": url,
        "snippet": snippet_text,
        "quote": quote_chunk,
        "source_agent": AgentName.DOCS.value,
        "tier": tier.value,
        "credibility": score,
        "published": "",
        "full_text": text[:MAX_EXTRACTED_CHARS],
    }


def _chunk_text_for_evidence(text: str, chunk_size: int = 900) -> list[str]:
    """Split text into overlapping chunks, similar to corpus notes."""
    if len(text) <= chunk_size:
        return [text]
    
    # Split on paragraph boundaries when possible
    paragraphs = text.split('\n\n')
    chunks: list[str] = []
    current = ""
    
    for para in paragraphs:
        if len(current) + len(para) > chunk_size and current:
            chunks.append(current.strip())
            # Keep some overlap
            current = para
        else:
            current += "\n\n" + para if current else para
    
    if current:
        chunks.append(current.strip())
    
    return chunks


def _select_best_quote_chunk(chunks: list[str]) -> str:
    """Select best chunk for quote, avoiding title pages."""
    if not chunks:
        return ""
    
    if len(chunks) == 1:
        return chunks[0][:500]
    
    # Score chunks by substantiveness (prefer chunks with full sentences and verbs)
    scored: list[tuple[float, str]] = []
    for i, chunk in enumerate(chunks[:5]):  # Check first 5 chunks
        # Penalize first chunk (likely title page)
        position_penalty = 0.3 if i == 0 else 0.0
        
        # Check for title page indicators
        lower = chunk.lower()
        is_title_page = (
            'abstract' in lower[:200] or
            'keywords:' in lower[:200] or
            'authors:' in lower[:200] or
            lower.count('\n') > len(chunk) / 30  # Many short lines = metadata
        )
        title_penalty = 0.5 if is_title_page else 0.0
        
        # Prefer chunks with verbs and full sentences
        verb_indicators = ['is', 'are', 'was', 'were', 'show', 'demonstrate', 'achieve', 'improve']
        verb_score = sum(1 for v in verb_indicators if f' {v} ' in chunk.lower()) / 10.0
        
        # Prefer chunks with numbers and specifics
        has_numbers = bool(re.search(r'\d+(?:\.\d+)?%|\d+\s*(?:ms|tokens?|parameters?)', chunk))
        number_score = 0.2 if has_numbers else 0.0
        
        score = verb_score + number_score - position_penalty - title_penalty
        scored.append((score, chunk))
    
    # Return best chunk, truncated
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1][:500] if scored else chunks[0][:500]
