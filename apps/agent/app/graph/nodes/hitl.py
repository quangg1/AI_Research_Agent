from __future__ import annotations

from langgraph.types import interrupt

from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event

# Human "Dig further" always gets one more research loop, even if the first
# pass burned the original budget arriving at HITL.
REVISE_EXTRA_ITERATIONS = 1
REVISE_EXTRA_TOOL_CALLS = 8
REVISE_MAX_ITERATIONS_CAP = 8
REVISE_MAX_TOOL_CALLS_CAP = 40


def hitl_node(state: ResearchState) -> dict:
    retrieved = state.get("retrieved") or state.get("evidence") or []
    payload = pythonize(
        {
            "type": "approve_report",
            "query": state.get("query"),
            "query_type": state.get("query_type"),
            "plan": state.get("plan"),
            "critic": state.get("critic"),
            "budget": state.get("budget"),
            "llm_mode": state.get("llm_mode"),
        }
    )
    event("hitl_interrupt", n_evidence=len(retrieved))
    decision = interrupt(payload)
    if isinstance(decision, str):
        decision = {"action": decision}
    action = (decision or {}).get("action", "approve")
    extra = (decision or {}).get("extra_questions") or []
    followups = []
    budget_patch: dict = {}
    if action == "revise":
        from app.domain.schema import AgentName, SubQuery

        for q in extra or [state["query"]]:
            followups.append(
                dump(SubQuery(agent=AgentName.SEARCH, question=q, rationale="Human revision"))
            )
        budget = budget_from(state)
        budget.max_iterations = min(
            REVISE_MAX_ITERATIONS_CAP,
            max(budget.max_iterations, budget.iterations + REVISE_EXTRA_ITERATIONS),
        )
        budget.max_tool_calls = min(
            REVISE_MAX_TOOL_CALLS_CAP,
            max(budget.max_tool_calls, budget.used_tool_calls + REVISE_EXTRA_TOOL_CALLS),
        )
        budget_patch = {"budget": dump(budget)}
        event(
            "hitl_revise_budget",
            iterations=budget.iterations,
            max_iterations=budget.max_iterations,
            used_tool_calls=budget.used_tool_calls,
            max_tool_calls=budget.max_tool_calls,
        )
    event("hitl_resume", action=action)
    return {
        "human_decision": decision or {"action": "approve"},
        "followups": followups,
        "status": "approved" if action == "approve" else "revising",
        **budget_patch,
        "traces": [{"node": "hitl", "action": action}],
    }
