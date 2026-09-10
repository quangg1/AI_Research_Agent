from __future__ import annotations

from urllib.parse import urlparse

from app.domain.research_intent import SECONDARY_HOSTS, is_secondary_host
from app.domain.schema import HOST_TIER, TIER_SCORE, SourceTier


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def tier_for(url: str, fallback: SourceTier | None = None) -> SourceTier:
    host = host_of(url)
    if not host:
        return fallback or SourceTier.UNKNOWN
    path = (urlparse(url).path or "").lower()
    # Path-aware demotion: HF model cards / blogs are not primary docs.
    if host.endswith("huggingface.co"):
        if path.startswith("/docs/") or "/docs/" in path:
            return SourceTier.OFFICIAL_REGULATION
        if path.startswith("/blog/") or path.startswith("/papers/"):
            return SourceTier.NEWS_ANALYSIS if "/blog/" in path else SourceTier.SPECIALIST_RESEARCH
        if path.startswith("/datasets/") or path.startswith("/spaces/"):
            return SourceTier.VENDOR_OR_CONSULTANCY
        # model / org roots → vendor, not "primary regulation"
        return SourceTier.VENDOR_OR_CONSULTANCY
    if host in {"github.com", "gitlab.com", "bitbucket.org"}:
        # Curated link lists are tertiary aggregators, not research papers.
        if "awesome" in path or "/awesome-" in path or path.rstrip("/").endswith("-list"):
            return SourceTier.NEWS_ANALYSIS
        return SourceTier.SPECIALIST_RESEARCH
    if is_secondary_host(host) or host in SECONDARY_HOSTS:
        return SourceTier.NEWS_ANALYSIS
    if host == "doi.org" and fallback is not None:
        # HOST_TIER's doi.org entry ("DOI host != peer venue") is a "we
        # don't actually know" placeholder for when nothing better is
        # available — unlike arxiv.org, which is a confident non-peer-review
        # classification. When a caller has real venue/type metadata (e.g.
        # OpenAlex's publisher record behind the DOI redirect) to pass as
        # fallback, trust that over the generic placeholder.
        return fallback
    if host in HOST_TIER:
        return HOST_TIER[host]
    parts = host.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in HOST_TIER:
            return HOST_TIER[candidate]
    return fallback or SourceTier.UNKNOWN


def credibility_score(url: str, published: str = "", fallback: SourceTier | None = None) -> tuple[SourceTier, float]:
    tier = tier_for(url, fallback)
    score = TIER_SCORE[tier]
    host = host_of(url)
    path = (urlparse(url).path or "").lower() if url else ""
    if host in {"github.com", "gitlab.com"}:
        if "awesome" in path or "/awesome-" in path:
            score = min(score, 0.38)
        else:
            score = max(min(score, 0.72), 0.55)
    if is_secondary_host(host):
        score = min(score, 0.40)
    year = _year(published)
    if year and year < 2020:
        score *= 0.85
    elif year and year < 2023:
        score *= 0.93
    return tier, round(min(score, 0.99), 3)


def _year(published: str) -> int | None:
    for token in published.replace("/", "-").split("-"):
        if token.isdigit() and len(token) == 4:
            value = int(token)
            if 1990 <= value <= 2035:
                return value
    return None
