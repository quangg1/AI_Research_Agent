from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field

WS_RE = re.compile(r"[^a-z0-9]+")
ARXIV_PATH_RE = re.compile(r"/(abs|pdf|html|pdf)/\d{4}\.\d+", re.I)
HOME_PATHS = {"", "/", "/en", "/en/latest", "/en/stable", "/docs", "/models", "/papers"}
NAV_CHROME_RE = re.compile(
    r"models\s+datasets\s+spaces|documentation index|fetch the complete documentation|buckets new docs enterprise",
    re.I,
)


class Citation(BaseModel):
    n: int
    evidence_id: str
    title: str = ""
    url: str = ""
    quote: str = ""
    tier: str = ""
    host: str = ""


def normalize(text: str) -> str:
    return WS_RE.sub(" ", (text or "").lower()).strip()


def quote_in_source(quote: str, source: str, min_overlap: float = 0.45) -> bool:
    """True if quote is a real span of the source (substring) or high token overlap."""
    q = normalize(quote)
    s = normalize(source)
    if not q or not s:
        return False
    if len(q) >= 12 and q in s:
        return True
    q_tokens = q.split()
    s_tokens = set(s.split())
    if not q_tokens:
        return False
    hit = sum(1 for t in q_tokens if t in s_tokens)
    return hit / len(q_tokens) >= min_overlap


def host_of(url: str) -> str:
    try:
        return urlparse(url).hostname.replace("www.", "") if url else ""
    except Exception:
        return ""


def is_citable_url(url: str) -> bool:
    """True for a specific landing page, not a site root like https://huggingface.co/."""
    raw = (url or "").strip()
    if raw.startswith("corpus://"):
        return True
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return False
    path = (parsed.path or "/").rstrip("/") or "/"
    if host in {"arxiv.org", "export.arxiv.org"}:
        return bool(ARXIV_PATH_RE.search(path + "/" + (parsed.fragment or ""))) or bool(
            re.search(r"/(abs|pdf|html)/\d{4}\.\d+", path)
        )
    if host == "doi.org":
        return path.startswith("/10.")
    parts = [p for p in path.split("/") if p]
    if path in HOME_PATHS or not parts:
        return False
    if len(parts) == 1 and parts[0] in {"en", "docs", "blog", "learn", "research", "intro"}:
        return False
    return True


def looks_like_nav_chrome(text: str) -> bool:
    t = " ".join((text or "").split())
    if len(t) < 40:
        return False
    return bool(NAV_CHROME_RE.search(t))


def pick_quote(ev: dict, limit: int = 280, *, claim_or_dimension: str = "", patterns: list[str] | None = None, topic_terms: list[str] | None = None) -> str:
    """Select best quote from evidence, optionally reranking by claim/dimension relevance.
    
    If claim_or_dimension is provided, will select the most relevant passage from
    the document rather than defaulting to the first chunk.
    """
    # If we have dimension-specific passage already selected, use it
    if ev.get("selected_passage"):
        blob = ev["selected_passage"]
    elif claim_or_dimension:
        # Use passage-level retrieval to find best chunk for this claim/dimension
        from app.retrieval.passage import best_passage_for_claim
        full_text = (
            f"{ev.get('full_text') or ''} "
            f"{ev.get('quote') or ''} "
            f"{ev.get('snippet') or ''}"
        ).strip()
        if full_text:
            best_passage = best_passage_for_claim(
                full_text,
                claim_or_dimension,
                patterns=patterns,
                topic_terms=topic_terms,
                max_passage_len=limit * 3,
            )
            if best_passage:
                blob = best_passage
            else:
                # Fallback to original logic
                blob = ev.get("quote") or ev.get("snippet") or ev.get("full_text") or ev.get("title") or ""
        else:
            blob = ev.get("quote") or ev.get("snippet") or ev.get("full_text") or ev.get("title") or ""
    else:
        # Original fallback logic when no claim specified
        raw = (ev.get("quote") or ev.get("snippet") or ev.get("full_text") or ev.get("title") or "").strip()
        skip = ("source:", "url:", "published:", "credibility:", "secondary:")
        lines = []
        for line in raw.splitlines():
            t = line.strip()
            if not t or t.startswith("#") or t.lower().startswith(skip):
                continue
            if looks_like_nav_chrome(t):
                continue
            lines.append(t)
        blob = " ".join(lines) or " ".join(raw.split())
    
    blob = " ".join(blob.split())
    if looks_like_nav_chrome(blob):
        alt = (ev.get("title") or "").strip()
        return alt[:limit] if alt else ""
    for sep in ".!?":
        if sep in blob[: limit + 40]:
            sent = blob.split(sep, 1)[0].strip()
            if len(sent) > 40:
                return sent + "."
    return blob[:limit]


def build_ledger(evidence: list[dict], k: int = 12) -> list[Citation]:
    from app.domain.coverage import canonical_source_key, dedupe_evidence, tag_evidence_roles
    from app.domain.research_intent import authority_score, demote_secondary

    seen: set[str] = set()
    out: list[Citation] = []
    cleaned = demote_secondary(tag_evidence_roles(dedupe_evidence(list(evidence or []))))
    cleaned = [e for e in cleaned if not e.get("off_topic")]
    ranked = sorted(
        cleaned,
        key=lambda e: (authority_score(e), float(e.get("credibility") or e.get("retrieval_score") or 0)),
        reverse=True,
    )
    n = 1
    for ev in ranked:
        eid = ev.get("id") or ev.get("url") or ""
        url = (ev.get("url") or "").strip()
        canon = canonical_source_key(url, ev.get("title") or "")
        if not eid or eid in seen or (canon and canon in seen):
            continue
        if url and not is_citable_url(url):
            continue
        seen.add(eid)
        if canon:
            seen.add(canon)
        quote = pick_quote(ev)
        if looks_like_nav_chrome(quote):
            continue
        blob = " ".join(str(ev.get(k) or "") for k in ("title", "snippet", "quote", "full_text"))
        if quote and not quote_in_source(quote, blob):
            quote = pick_quote({**ev, "quote": ev.get("snippet") or ev.get("title") or ""})
        out.append(
            Citation(
                n=n,
                evidence_id=str(eid),
                title=ev.get("title") or "",
                url=ev.get("url") or "",
                quote=quote,
                tier=ev.get("tier") or "",
                host=host_of(ev.get("url") or ""),
            )
        )
        n += 1
        if len(out) >= k:
            break
    return out


def index_by_id(citations: list[Citation]) -> dict[str, Citation]:
    return {c.evidence_id: c for c in citations}


def cite_marker(n: int) -> str:
    return f"[{n}]"


def _as_dict(c) -> dict:
    return c if isinstance(c, dict) else c.model_dump(mode="json")


def format_reference_list(citations: list) -> str:
    lines = []
    for raw in citations:
        c = _as_dict(raw)
        n = c.get("n")
        title = (c.get("title") or c.get("url") or "source").strip()
        url = (c.get("url") or "").strip()
        if url and is_citable_url(url):
            lines.append(f"{n}. [{title}]({url}) — `{url}`")
        else:
            lines.append(f"{n}. {title}")
    return "\n".join(lines)


TIER_INLINE = {
    "peer_reviewed": "peer",
    "official_regulation": "primary",
    "standard_body": "primary",
    "intergovernmental": "primary",
    "specialist_research": "specialist",
    "industry_association": "industry",
    "news_analysis": "news",
    "vendor_or_consultancy": "vendor",
}

INLINE_TIER_WORDS = frozenset(TIER_INLINE.values()) | {"repo"}
MULTI_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def inline_tier_label(citation: dict) -> str:
    url = (citation.get("url") or "").lower()
    if "github.com" in url or "gitlab.com" in url:
        return "repo"
    tier = (citation.get("tier") or "").strip().lower()
    return TIER_INLINE.get(tier, "")


def annotate_inline_citation_tiers(md: str, citations: list) -> str:
    """Transform [3] → [3 peer] using ledger tier metadata."""
    by_n = {_as_dict(c).get("n"): _as_dict(c) for c in citations if _as_dict(c).get("n")}

    def _annotate_inner(inner: str) -> str:
        if re.search(r"\d+\s+(?:" + "|".join(INLINE_TIER_WORDS) + r")\b", inner, re.I):
            return inner
        parts: list[str] = []
        for token in re.split(r"\s*,\s*", inner):
            token = token.strip()
            if not token.isdigit():
                parts.append(token)
                continue
            n = int(token)
            label = inline_tier_label(by_n.get(n) or {})
            parts.append(f"{n} {label}" if label else str(n))
        return ", ".join(parts)

    def _repl(match: re.Match) -> str:
        inner = match.group(1)
        if "## References" in (md[max(0, match.start() - 80) : match.start()]):
            return match.group(0)
        return f"[{_annotate_inner(inner)}]"

    return MULTI_CITE_RE.sub(_repl, md or "")


def bind_markdown_to_ledger(md: str, citations: list) -> str:
    """Drop invented links and bind exactly one References section."""
    allowed = {( _as_dict(c).get("url") or "").strip() for c in citations}
    allowed.discard("")

    def _keep_link(match: re.Match) -> str:
        text, url = match.group(1), match.group(2).strip()
        if url in allowed:
            return match.group(0)
        return text

    md = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _keep_link, md or "")
    refs = format_reference_list(citations)
    reference_heading = re.search(r"(?im)^##\s+(?:Core\s+references|References)\s*$", md)
    if reference_heading:
        head = md[: reference_heading.start()].rstrip()
        md = f"{head}\n\n## References\n\n{refs}\n"
    elif refs:
        md = md.rstrip() + "\n\n## References\n\n" + refs + "\n"
    return md
