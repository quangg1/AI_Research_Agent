from __future__ import annotations

from app.domain.citations import is_citable_url
from app.domain.retrieval_limits import QDRANT_TOP_K, RETRIEVE_TOP_K
from app.domain.coverage import tag_evidence_roles
from app.domain.research_intent import demote_secondary
from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState, budget_from
from app.llm.client import llm
from app.observability.logging import event
from app.retrieval.hybrid import hybrid_retrieve, term_overlap
from app.retrieval.store import search_qdrant

TOOL_AGENTS = {"search", "scholar", "docs"}


def collector_node(state: ResearchState) -> dict:
    evidence = tag_evidence_roles(state.get("evidence") or [], state.get("query") or "")
    budget = budget_from(state)
    ran = [getattr(a, "value", a) for a in (state.get("agents_to_run") or [])]
    tool_agents = [a for a in ran if a in TOOL_AGENTS]
    external_calls = _latest_external_calls(state.get("traces") or [])
    charged_calls = sum(external_calls) if external_calls else len(tool_agents)
    budget.used_retrieval_calls += charged_calls
    budget.sync_totals()
    event("collector", n=len(evidence), used_tool_calls=budget.used_tool_calls, used_retrieval=budget.used_retrieval_calls)
    return {
        "evidence": evidence,
        "status": "collected",
        "budget": dump(budget),
        "traces": [
            {
                "node": "collector",
                "n": len(evidence),
                "agents_ran": tool_agents,
                "external_calls": charged_calls,
            }
        ],
    }


def _latest_external_calls(traces: list[dict]) -> list[int]:
    latest: dict[str, dict] = {}
    for trace in reversed(traces):
        node = trace.get("node")
        if node in TOOL_AGENTS and node not in latest:
            latest[node] = trace
    return [
        max(0, int(trace.get("external_calls") or 0))
        for trace in latest.values()
        if "external_calls" in trace
    ]


def retrieve_node(state: ResearchState) -> dict:
    query = state["query"]
    evidence = tag_evidence_roles(list(state.get("evidence") or []), query)
    extra = search_qdrant(query, k=QDRANT_TOP_K) or []
    seen = {e.get("id") for e in evidence}
    for hit in extra:
        blob = f"{hit.get('title', '')} {hit.get('snippet', '')}"
        if term_overlap(query, blob) < 0.18:
            continue
        if hit.get("id") not in seen:
            evidence.append(hit)
            seen.add(hit.get("id"))
    evidence = tag_evidence_roles([e for e in evidence if is_citable_url(e.get("url") or "")], query)
    evidence = demote_secondary(evidence)
    on_topic = [e for e in evidence if not e.get("off_topic")]
    pool = on_topic if on_topic else evidence
    ranked = (
        hybrid_retrieve(
            query,
            pool,
            k=min(RETRIEVE_TOP_K, max(8, len(pool))),
            use_llm_reranker=llm.available,
        )
        if pool
        else []
    )
    rerank_calls = max((int(row.get("rerank_external_calls") or 0) for row in ranked), default=0)
    return {
        "retrieved": pythonize(ranked),
        "status": "retrieved",
        "traces": [
            {
                "node": "retrieve",
                "n": len(ranked),
                "off_topic_dropped": len(evidence) - len(pool),
                "rerank_external_calls": rerank_calls,
            }
        ],
    }
