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
    """Per-dimension hybrid retrieval: each must-answer slot gets its own embed+lexical retrieve.
    
    Quality-first: retrieve k=3-5 per slot with slot-specific patterns/terms, not one global top-20.
    """
    query = state["query"]
    evidence = tag_evidence_roles(list(state.get("evidence") or []), query)
    
    # Add Qdrant corpus pool
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
    
    # Per-dimension retrieval
    critic = state.get("critic") or {}
    coverage = critic.get("coverage") or {}
    slots = coverage.get("slots") or []
    
    if not slots:
        # Fallback to deriving slots from query if critic hasn't run yet
        from app.domain.coverage import must_answer_for
        slots = must_answer_for(query)
    
    if not pool:
        return {
            "retrieved": [],
            "status": "retrieved",
            "traces": [{"node": "retrieve", "n": 0, "off_topic_dropped": len(evidence)}],
        }
    
    # Retrieve per dimension
    ranked_by_slot: list[dict] = []
    retrieved_ids: set[str] = set()
    total_rerank_calls = 0
    
    from app.domain.coverage import _anchors, _blob
    anchors = _anchors(query)
    
    for slot in slots:
        slot_id = slot.get("id") or ""
        slot_label = slot.get("label") or ""
        status = slot.get("status") or "open"
        
        # Skip already-covered dimensions (critic marked as sufficient)
        if status in {"covered", "sufficient"}:
            continue
        
        # Build slot-specific query
        patterns = [p for p in (slot.get("patterns") or []) if p]
        topic_terms = [t for t in (slot.get("topic_terms") or anchors) if t]
        
        # Slot query: combine label + top patterns
        slot_query_parts = [slot_label] + patterns[:2]
        slot_query = " ".join(slot_query_parts).strip() or query
        
        # Hybrid retrieve for this dimension (embed + lexical)
        # k=5 per slot (more than the 3 that dossier will use, to allow reranking)
        slot_candidates = hybrid_retrieve(
            slot_query,
            pool,
            k=min(5, len(pool)),
            use_llm_reranker=False,  # Rerank per-slot below if enabled
        )
        
        # Optional: LLM rerank within this slot's candidates (not global)
        if llm.available and slot_candidates:
            slot_candidates = hybrid_retrieve(
                slot_query,
                slot_candidates,
                k=min(5, len(slot_candidates)),
                use_llm_reranker=True,
            )
            slot_rerank_calls = max(
                (int(c.get("rerank_external_calls") or 0) for c in slot_candidates),
                default=0
            )
            total_rerank_calls += slot_rerank_calls
        
        # Tag each with slot_id and add to union
        for candidate in slot_candidates:
            eid = candidate.get("id") or ""
            if eid and eid not in retrieved_ids:
                candidate["slot_id"] = slot_id
                candidate["dimension_label"] = slot_label
                ranked_by_slot.append(candidate)
                retrieved_ids.add(eid)
    
    # If no dimensions or all covered, fall back to global retrieve
    if not ranked_by_slot:
        ranked_by_slot = hybrid_retrieve(
            query,
            pool,
            k=min(RETRIEVE_TOP_K, max(8, len(pool))),
            use_llm_reranker=llm.available,
        )
        total_rerank_calls = max(
            (int(r.get("rerank_external_calls") or 0) for r in ranked_by_slot),
            default=0
        )
    
    event(
        "retrieve_per_dimension",
        n=len(ranked_by_slot),
        slots=len([s for s in slots if s.get("status") not in {"covered", "sufficient"}]),
    )
    
    return {
        "retrieved": pythonize(ranked_by_slot),
        "status": "retrieved",
        "traces": [
            {
                "node": "retrieve",
                "n": len(ranked_by_slot),
                "off_topic_dropped": len(evidence) - len(pool),
                "rerank_external_calls": total_rerank_calls,
                "per_dimension": True,
                "open_slots": len([s for s in slots if s.get("status") not in {"covered", "sufficient"}]),
            }
        ],
    }
