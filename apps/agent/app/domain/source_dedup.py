"""Near-duplicate collapse before expensive deep fetch."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.domain.coverage import dedupe_evidence, normalize_work_title, work_identity
from app.domain.evidence_pipeline import access_allows_fetch, expected_fetch_priority

_STOP = frozenset(
    "a an the and or for to of in on with vs is are be how what when why which that this these those "
    "from into about over under than then also only very said says according".split()
)


def _excerpt(ev: dict) -> str:
    return " ".join(
        str(ev.get(key) or "")
        for key in ("title", "search_snippet", "snippet", "quote")
    ).strip()


def _token_set(text: str) -> set[str]:
    return {w for w in re.split(r"\W+", (text or "").lower()) if len(w) > 3 and w not in _STOP}


def excerpt_similarity(a: str, b: str) -> float:
    if not a.strip() or not b.strip():
        return 0.0
    ta, tb = _token_set(a), _token_set(b)
    if ta and tb:
        return len(ta & tb) / max(1, len(ta | tb))
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def is_near_duplicate(ev: dict, kept: list[dict], *, threshold: float = 0.58) -> bool:
    """True when this source is syndicated/near-duplicate of an already-kept candidate."""
    blob = _excerpt(ev)
    if not blob:
        return False
    nt = normalize_work_title(str(ev.get("title") or ""))
    wid = work_identity(str(ev.get("url") or ""), str(ev.get("title") or ""))
    for other in kept:
        if work_identity(str(other.get("url") or ""), str(other.get("title") or "")) == wid:
            return True
        other_nt = normalize_work_title(str(other.get("title") or ""))
        if nt and other_nt and len(nt) >= 24 and SequenceMatcher(None, nt, other_nt).ratio() >= 0.9:
            return True
        if excerpt_similarity(blob, _excerpt(other)) >= threshold:
            return True
    return False


def select_urls_for_enrich(
    evidence: list[dict],
    urls: list[str],
    *,
    similarity_threshold: float = 0.58,
) -> tuple[list[str], dict[str, Any]]:
    """Dedup + access gate + priority rank before deep fetch."""
    by_url = {(e.get("url") or ""): e for e in evidence if e.get("url")}
    ranked = sorted(
        urls,
        key=lambda u: (-expected_fetch_priority(by_url.get(u) or {"url": u}, u), u),
    )
    kept_urls: list[str] = []
    kept_rows: list[dict] = []
    stats = {"dropped_duplicate": 0, "dropped_access": 0, "input": len(urls), "kept": 0}
    for url in ranked:
        ev = dict(by_url.get(url) or {"url": url})
        if not access_allows_fetch(url, ev):
            stats["dropped_access"] += 1
            continue
        if is_near_duplicate(ev, kept_rows, threshold=similarity_threshold):
            stats["dropped_duplicate"] += 1
            continue
        kept_urls.append(url)
        kept_rows.append(ev)
    stats["kept"] = len(kept_urls)
    return kept_urls, stats


def unique_source_count(evidence: list[dict]) -> int:
    return len(dedupe_evidence(evidence or []))
