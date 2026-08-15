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
    if host in {"github.com", "gitlab.com"}:
        return SourceTier.SPECIALIST_RESEARCH
    if is_secondary_host(host) or host in SECONDARY_HOSTS:
        return SourceTier.NEWS_ANALYSIS
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
    if host in {"github.com", "gitlab.com"}:
        score = max(score, 0.80)
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
