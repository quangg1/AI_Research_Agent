from __future__ import annotations

from langgraph.types import interrupt

from app.domain.schema import AgentName, SubQuery
from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event


def plan_gate_node(state: ResearchState) -> dict:
    """Optional human review of planner output before retrieval agents run."""
    if state.get("plan_confirmed"):
        return {"traces": [{"node": "plan_gate", "skipped": "confirmed"}]}

    budget = budget_from(state)
    brief = state.get("brief") or {}
    depth = str(brief.get("depth") or "standard").lower()
    if budget.iterations > 1 or depth == "quick" or state.get("reuse_mode"):
        return {
            "plan_confirmed": True,
            "traces": [{"node": "plan_gate", "skipped": "auto", "depth": depth}],
        }

    plan = state.get("plan") or {}
    payload = pythonize(
        {
            "type": "plan_review",
            "title": "Agent plan",
            "subtitle": "Review sub-queries and agents before Kiln starts searching.",
            "query": state.get("query"),
            "plan": plan,
            "agents_to_run": state.get("agents_to_run") or [],
            "sub_queries": plan.get("sub_queries") or [],
        }
    )
    event("plan_gate_interrupt", agents=state.get("agents_to_run"))
    decision = interrupt(payload)
    if isinstance(decision, str):
        decision = {"action": decision}

    action = (decision or {}).get("action", "start")
    if action == "cancel":
        return {
            "status": "cancelled",
            "plan_confirmed": False,
            "human_decision": decision or {"action": "cancel"},
            "traces": [{"node": "plan_gate", "action": "cancel"}],
        }

    patch = (decision or {}).get("plan") or {}
    merged_plan = dict(plan)
    if isinstance(patch, dict):
        for key in ("sub_queries", "agents_to_run", "goal", "assumptions", "stop_conditions"):
            if patch.get(key) is not None:
                merged_plan[key] = patch[key]

    agents = list(merged_plan.get("agents_to_run") or state.get("agents_to_run") or [])
    agents = [str(a) for a in agents if str(a) in {a.value for a in AgentName}]
    if not agents:
        agents = list(state.get("agents_to_run") or [])

    sub_queries = []
    for raw in merged_plan.get("sub_queries") or []:
        try:
            sub_queries.append(dump(SubQuery.model_validate(raw)))
        except Exception:
            continue

    event("plan_gate_confirmed", action=action, agents=agents, sub_queries=len(sub_queries))
    return {
        "plan": {**merged_plan, "sub_queries": sub_queries or merged_plan.get("sub_queries") or []},
        "agents_to_run": agents,
        "plan_confirmed": True,
        "human_decision": decision or {"action": "start"},
        "traces": [{"node": "plan_gate", "action": action, "agents": agents, "sub_queries": len(sub_queries)}],
    }


def plan_gate_node_auto(state: ResearchState) -> dict:
    return {"plan_confirmed": True, "traces": [{"node": "plan_gate", "skipped": "auto"}]}
