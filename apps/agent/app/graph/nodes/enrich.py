from __future__ import annotations

from app.domain.adversarial import numeric_evidence_score
from app.domain.citations import is_citable_url
from app.domain.research_depth import effective_depth
from app.domain.gap_enrich import coverage_slots_from_state, urls_for_gap_slots
from app.domain.retrieval_limits import ENRICH_FETCH_CAP
from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event
from app.observability.node_trace import trace_span
from app.tools.fetch import evidence_from_url, is_fetchable, sanitize_fetched_content

_PRIMARY_HOST_MARKERS = ("arxiv.org", "doi.org", "github.com", "gitlab.com", "openreview.net")
_OFFICIAL_DOC_MARKERS = ("docs.", ".gov", "openai.com", "anthropic.com", "google.com", "microsoft.com")


def enrich_node(state: ResearchState) -> dict:
    budget = budget_from(state)
    brief = state.get("brief") or {}
    depth = effective_depth(brief)
    evidence = list(state.get("evidence") or [])
    urls = []
    for ev in evidence:
        url = ev.get("url") or ""
        if is_citable_url(url) and is_fetchable(url) and url not in urls:
            urls.append(url)

    fetch_cap = _enrich_cap(depth, budget.iterations)
    slots = coverage_slots_from_state(state)
    slot_mode = budget.iterations > 1 and bool(gap_slots_with_gaps(slots))
    if slot_mode:
        gap_urls = urls_for_gap_slots(evidence, slots, limit=max(fetch_cap, 6))
        rest = [u for u in _prioritize_enrich_urls(evidence, urls) if u not in gap_urls]
        urls = gap_urls + rest
    else:
        urls = _prioritize_enrich_urls(evidence, urls)

    fetched = 0
    extra: list[dict] = []
    with trace_span("enrich", active_agent="enrich") as span:
        for url in urls[:fetch_cap]:
            if budget.remaining_enrich_calls <= 0:
                break
            existing = next((e for e in evidence if e.get("url") == url), {})
            if len(existing.get("full_text") or existing.get("snippet") or "") >= 1600:
                continue
            row = evidence_from_url(url, title=existing.get("title") or "")
            budget.used_enrich_calls += 1
            budget.sync_totals()
            fetched += 1
            if not row:
                continue
            if row.get("full_text"):
                row["full_text"] = sanitize_fetched_content(str(row["full_text"]))
            if row.get("snippet"):
                row["snippet"] = sanitize_fetched_content(str(row["snippet"]))[:1600]
            extra.append(row)
        span["fetched"] = fetched
        span["n"] = len(extra)
        span["external_calls"] = fetched
        span["slot_aware"] = slot_mode
    event("enrich", fetched=fetched, extra=len(extra), cap=fetch_cap, slot_aware=slot_mode)
    return {
        "evidence": pythonize(extra),
        "budget": dump(budget),
        "traces": [span],
    }


def gap_slots_with_gaps(slots: list[dict]) -> list[dict]:
    return [s for s in slots if s.get("status") in {"open", "weak"}]


def _enrich_cap(depth: str, iteration: int) -> int:
    """First pass: fetch primaries first — leave budget for critic gap loops."""
    first, later = ENRICH_FETCH_CAP.get(depth, ENRICH_FETCH_CAP["standard"])
    return first if iteration <= 1 else later


def _prioritize_enrich_urls(evidence: list[dict], urls: list[str]) -> list[str]:
    def rank(url: str) -> tuple[int, float, int]:
        low = url.lower()
        tier = 0
        if "arxiv.org" in low or "doi.org" in low or "openreview.net" in low:
            tier = 4
        elif "github.com" in low or "gitlab.com" in low:
            tier = 3
        elif any(m in low for m in _OFFICIAL_DOC_MARKERS):
            tier = 2
        ev = next((e for e in evidence if e.get("url") == url), {})
        snippet_len = len(ev.get("full_text") or ev.get("snippet") or "")
        numeric = numeric_evidence_score(ev)
        return (-tier, -numeric, snippet_len)

    return sorted(urls, key=rank)
