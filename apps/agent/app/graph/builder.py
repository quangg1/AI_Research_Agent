from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.briefing import briefing_node, briefing_node_auto
from app.graph.nodes.collector import collector_node, retrieve_node
from app.graph.nodes.critic import critic_node
from app.graph.nodes.docs import docs_node
from app.graph.nodes.enrich import enrich_node
from app.graph.nodes.extract import extract_node
from app.graph.nodes.hitl import hitl_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.report import report_node
from app.graph.nodes.scholar import scholar_node
from app.graph.nodes.search import search_node
from app.graph.state import ResearchState, budget_from


def after_briefing(state: ResearchState) -> str:
    if state.get("out_of_scope") or state.get("status") == "cancelled":
        return "report"
    return "planner"


def after_planner(state: ResearchState) -> list[str] | str:
    if state.get("out_of_scope"):
        return "report"
    if state.get("reuse_mode") == "cached" and state.get("prior_knowledge"):
        # A stored answer already covers this question; skip retrieval entirely.
        return "report"
    agents = state.get("agents_to_run") or []
    if not agents:
        return "collector"
    # Always enter the three research nodes so the join at collector is well-defined.
    # Nodes that are not selected return immediately (adaptive skip).
    return ["search", "scholar", "docs"]


def after_critic(state: ResearchState, hitl_target: str = "hitl") -> str:
    budget = budget_from(state)
    critic = state.get("critic") or {}
    status = critic.get("status")
    followups = critic.get("followup_queries") or state.get("followups") or []
    if budget.exhausted or budget.remaining_calls <= 0:
        return hitl_target
    if status == "sufficient":
        return hitl_target
    if followups and budget.remaining_iterations > 0:
        return "planner"
    return hitl_target


def after_critic_eval(state: ResearchState) -> str:
    route = after_critic(state, hitl_target="report")
    return "report" if route == "hitl" else route


def after_hitl(state: ResearchState) -> str:
    decision = state.get("human_decision") or {}
    # Dig further always re-enters the planner; hitl_node already grants revise headroom.
    if decision.get("action") == "revise":
        return "planner"
    return "report"


def build_graph(checkpointer=None, enable_hitl: bool = True, *, allow_memory: bool = False):
    if checkpointer is None:
        if not allow_memory:
            raise RuntimeError("A durable graph checkpointer is required")
        checkpointer = MemorySaver()
    builder = StateGraph(ResearchState)
    builder.add_node("briefing", briefing_node if enable_hitl else briefing_node_auto)
    builder.add_node("planner", planner_node)
    builder.add_node("search", search_node)
    builder.add_node("scholar", scholar_node)
    builder.add_node("docs", docs_node)
    builder.add_node("collector", collector_node)
    builder.add_node("enrich", enrich_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("extract", extract_node)
    builder.add_node("critic", critic_node)
    builder.add_node("report", report_node)

    builder.add_edge(START, "briefing")
    builder.add_conditional_edges(
        "briefing",
        after_briefing,
        {"planner": "planner", "report": "report"},
    )
    builder.add_conditional_edges(
        "planner",
        after_planner,
        {"search": "search", "scholar": "scholar", "docs": "docs", "collector": "collector", "report": "report"},
    )
    builder.add_edge(["search", "scholar", "docs"], "collector")
    builder.add_edge("collector", "enrich")
    builder.add_edge("enrich", "retrieve")
    builder.add_edge("retrieve", "extract")
    builder.add_edge("extract", "critic")
    if enable_hitl:
        builder.add_node("hitl", hitl_node)
        builder.add_conditional_edges("critic", after_critic, {"planner": "planner", "hitl": "hitl"})
        builder.add_conditional_edges("hitl", after_hitl, {"planner": "planner", "report": "report"})
    else:
        builder.add_conditional_edges("critic", after_critic_eval, {"planner": "planner", "report": "report"})
    builder.add_edge("report", END)
    return builder.compile(checkpointer=checkpointer)


def build_test_graph(enable_hitl: bool = True):
    return build_graph(enable_hitl=enable_hitl, allow_memory=True)
