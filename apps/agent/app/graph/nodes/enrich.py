from __future__ import annotations

from app.domain.schema import AgentName
from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event
from app.tools.fetch import evidence_from_url, is_allowed


def enrich_node(state: ResearchState) -> dict:
    budget = budget_from(state)
    evidence = list(state.get("evidence") or [])
    urls = []
    for ev in evidence:
        url = ev.get("url") or ""
        if is_allowed(url) and url not in urls:
            urls.append(url)
    fetched = 0
    extra: list[dict] = []
    for url in urls[:12]:
        if budget.remaining_calls <= 0:
            break
        existing = next((e for e in evidence if e.get("url") == url), {})
        if len(existing.get("full_text") or existing.get("snippet") or "") >= 1600:
            continue
        row = evidence_from_url(url, title=existing.get("title") or "")
        budget.used_tool_calls += 1
        fetched += 1
        if not row:
            continue
        extra.append(row)
    event("enrich", fetched=fetched, extra=len(extra))
    return {
        "evidence": pythonize(extra),
        "budget": dump(budget),
        "traces": [{"node": "enrich", "fetched": fetched, "n": len(extra)}],
    }
