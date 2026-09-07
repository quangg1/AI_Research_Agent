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
from app.graph.nodes.memo_gate import memo_gate_node, memo_gate_node_auto
from app.graph.nodes.plan_gate import plan_gate_node, plan_gate_node_auto
from app.graph.nodes.planner import planner_node
from app.graph.nodes.report import report_node
from app.graph.nodes.scholar import scholar_node
from app.graph.nodes.search import search_node
from app.graph.state import ResearchState, budget_from


def after_briefing(state: ResearchState) -> str:
    if state.get("out_of_scope") or state.get("status") == "cancelled":
        return "report"
    return "planner"


def after_planner(state: ResearchState) -> str:
    if state.get("out_of_scope"):
        return "report"
    if state.get("reuse_mode") == "cached" and state.get("prior_knowledge"):
        return "report"
    return "plan_gate"


def after_plan_gate(state: ResearchState) -> list[str] | str:
    if state.get("out_of_scope") or state.get("status") == "cancelled":
        return "report"
    if state.get("reuse_mode") == "cached" and state.get("prior_knowledge"):
        return "report"
    agents = state.get("agents_to_run") or []
    if not agents:
        return "collector"
    return ["search", "scholar", "docs"]


def after_critic(state: ResearchState, hitl_target: str = "hitl") -> str:
    budget = budget_from(state)
    critic = state.get("critic") or {}
    status = critic.get("status")
    followups = critic.get("followup_queries") or state.get("followups") or []
    
    # Early exit checks
    if budget.exhausted or budget.remaining_calls <= 0:
        return hitl_target
    if status == "sufficient":
        return hitl_target
    
    # Quality-aware early stopping for production efficiency
    if followups and budget.remaining_iterations > 0:
        coverage = critic.get("coverage") or {}
        depth_score = (critic.get("depth_score") or {}).get("score") or 0
        must_pct = (critic.get("depth_score") or {}).get("must_answer", {}).get("pct") or 0
        current_sources = coverage.get("unique_sources") or 0
        iteration = budget.iterations
        
        # Track quality history for smart stopping
        quality_history = state.get("_quality_history") or []
        quality_history.append({
            "iteration": iteration,
            "unique_sources": current_sources,
            "must_pct": must_pct,
            "depth_score": depth_score,
        })
        
        # EARLY STOP 1: Quality already excellent (save budget)
        if must_pct >= 75 and depth_score >= 80:
            from app.observability.logging import event
            event(
                "critic_early_stop_quality_sufficient",
                iteration=iteration,
                must_pct=must_pct,
                depth_score=depth_score,
                message="Quality already excellent - stopping to save budget"
            )
            return hitl_target
        
        # EARLY STOP 2: No improvement in last 2 iterations (stagnation)
        if len(quality_history) >= 3:
            recent = quality_history[-3:]
            
            # Check if sources, coverage, AND score are stagnant
            sources_stagnant = (
                recent[0]["unique_sources"] == recent[1]["unique_sources"] == recent[2]["unique_sources"]
            )
            # FIXED: Lower threshold to 2% - only flag TRUE stagnation, not minor improvement
            # (e.g., 60% → 64% → 68% is +4%/iter = good progress, should NOT stop)
            coverage_stagnant = (
                abs(recent[2]["must_pct"] - recent[1]["must_pct"]) < 2
                and abs(recent[1]["must_pct"] - recent[0]["must_pct"]) < 2
            )
            score_stagnant = (
                abs(recent[2]["depth_score"] - recent[1]["depth_score"]) < 3
                and abs(recent[1]["depth_score"] - recent[0]["depth_score"]) < 3
            )
            
            if sources_stagnant and coverage_stagnant and score_stagnant:
                from app.observability.logging import event
                event(
                    "critic_stagnation_detected",
                    iteration=iteration,
                    unique_sources=current_sources,
                    must_pct=must_pct,
                    depth_score=depth_score,
                    stagnant_iterations=3,
                    remaining_gaps=len(coverage.get("critical_gaps") or []),
                    message="No quality improvement in 3 iterations - stopping to save budget"
                )
                return hitl_target
        
        # Continue iterating if still improving or not yet stagnant
        return "planner"
    
    return hitl_target


def after_critic_eval(state: ResearchState) -> str:
    route = after_critic(state, hitl_target="report")
    return "report" if route == "hitl" else route


def after_hitl(state: ResearchState) -> str:
    decision = state.get("human_decision") or {}
    if decision.get("action") == "revise":
        return "planner"
    return "report"


def after_memo_gate(state: ResearchState) -> str:
    status = state.get("status")
    if status == "revising":
        return "critic"
    if status == "revising_quality":
        # Quality issues detected - go back to report to rewrite from notes
        return "report"
    return END


def after_report(state: ResearchState) -> str:
    if state.get("status") == "integrity_research":
        return "planner"
    return "memo_gate"


def build_graph(checkpointer=None, enable_hitl: bool = True, *, allow_memory: bool = False):
    if checkpointer is None:
        if not allow_memory:
            raise RuntimeError("A durable graph checkpointer is required")
        checkpointer = MemorySaver()
    builder = StateGraph(ResearchState)
    builder.add_node("briefing", briefing_node if enable_hitl else briefing_node_auto)
    builder.add_node("planner", planner_node)
    builder.add_node("plan_gate", plan_gate_node if enable_hitl else plan_gate_node_auto)
    builder.add_node("search", search_node)
    builder.add_node("scholar", scholar_node)
    builder.add_node("docs", docs_node)
    builder.add_node("collector", collector_node)
    builder.add_node("enrich", enrich_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("extract", extract_node)
    builder.add_node("critic", critic_node)
    builder.add_node("report", report_node)
    builder.add_node("memo_gate", memo_gate_node if enable_hitl else memo_gate_node_auto)

    builder.add_edge(START, "briefing")
    builder.add_conditional_edges(
        "briefing",
        after_briefing,
        {"planner": "planner", "report": "report"},
    )
    builder.add_conditional_edges(
        "planner",
        after_planner,
        {"plan_gate": "plan_gate", "report": "report"},
    )
    builder.add_conditional_edges(
        "plan_gate",
        after_plan_gate,
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
    builder.add_conditional_edges("memo_gate", after_memo_gate, {"critic": "critic", "report": "report", END: END})
    builder.add_conditional_edges(
        "report",
        after_report,
        {"planner": "planner", "memo_gate": "memo_gate"},
    )
    return builder.compile(checkpointer=checkpointer)


def build_test_graph(enable_hitl: bool = True):
    return build_graph(enable_hitl=enable_hitl, allow_memory=True)
