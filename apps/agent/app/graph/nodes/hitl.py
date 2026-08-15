from __future__ import annotations

from langgraph.types import interrupt

from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState
from app.observability.logging import event


def hitl_node(state: ResearchState) -> dict:
    retrieved = state.get("retrieved") or state.get("evidence") or []
    payload = pythonize({
        "type": "approve_report",
        "query": state.get("query"),
        "query_type": state.get("query_type"),
        "plan": state.get("plan"),
        "critic": state.get("critic"),
        "budget": state.get("budget"),
        "llm_mode": state.get("llm_mode"),
        "claims_preview": (state.get("claims") or [])[:4],
        "evidence_preview": [
            {
                "id": e.get("id"),
                "title": e.get("title"),
                "url": e.get("url"),
                "tier": e.get("tier"),
                "credibility": float(e.get("credibility") or 0),
                "snippet": (e.get("snippet") or "")[:280],
            }
            for e in retrieved[:8]
        ],
    })
    event("hitl_interrupt", n_evidence=len(retrieved))
    decision = interrupt(payload)
    if isinstance(decision, str):
        decision = {"action": decision}
    action = (decision or {}).get("action", "approve")
    extra = (decision or {}).get("extra_questions") or []
    followups = []
    if action == "revise":
        from app.domain.schema import AgentName, SubQuery

        for q in extra or [state["query"]]:
            followups.append(dump(SubQuery(agent=AgentName.SEARCH, question=q, rationale="Human revision")))
    event("hitl_resume", action=action)
    return {
        "human_decision": decision or {"action": "approve"},
        "followups": followups,
        "status": "approved" if action == "approve" else "revising",
        "traces": [{"node": "hitl", "action": action}],
    }
