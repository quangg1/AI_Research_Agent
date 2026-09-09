from __future__ import annotations

import asyncio
import re

from langgraph.types import interrupt

from app.domain.adversarial import competing_hypotheses, research_subquestions
from app.domain.research_depth import apply_forced_depth, effective_depth
from app.domain.routing_policy import classify_query, out_of_scope
from app.domain.coverage import must_answer_for
from app.domain.research_intent import is_comparison_query, is_mechanism_query, must_cover_for
from app.domain.schema import QueryType, ResearchBrief
from app.domain.research_contract import compile_research_contract
from app.domain.textutil import entity_candidates, user_goal
from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState, budget_from
from app.llm.client import llm
from app.llm.roles import use_role_model
from app.observability.logging import event

SECTOR_RE = re.compile(
    r"\b(rag|retrieval|serving|vllm|quantization|fine-?tune|agent|eval|observability|embedding|rerank|tool calling|prompt injection)\b",
    re.I,
)
STACK_RE = re.compile(
    r"\b(openai|anthropic|gemini|huggingface|vllm|langchain|qdrant|postgres|aws|gcp|azure)\b",
    re.I,
)




def _brief_with_contract(query: str, brief: ResearchBrief) -> dict:
    """Dump brief and attach compiled research_contract without breaking pydantic."""
    data = dump(brief)
    try:
        contract = compile_research_contract(query, data)
        data["research_contract"] = contract.to_dict()
        # Prefer contract-aligned must-answer for LoRA/QLoRA/FT queries.
        from app.domain.research_contract import must_answer_from_contract

        contract_slots = must_answer_from_contract(query, contract, brief=data)
        if contract_slots:
            data["must_answer"] = contract_slots
        else:
            data["must_answer"] = must_answer_for(query, brief=data)
    except Exception:
        # Fail-soft: briefing must not die if contract compile fails.
        data.setdefault("research_contract", {})
    return data

async def briefing_node(state: ResearchState) -> dict:
    """Build an editable research brief, then pause for user confirm (Deep Research style)."""
    if state.get("brief_confirmed"):
        return {
            "status": "researching",
            "traces": [{"node": "briefing", "skipped": "already_confirmed"}],
        }

    query = state["query"]
    if out_of_scope(query):
        return {
            "out_of_scope": True,
            "status": "out_of_scope",
            "brief_confirmed": True,
            "traces": [{"node": "briefing", "decision": "out_of_scope"}],
        }

    raw = await asyncio.to_thread(_resolve_brief, query)
    budget = budget_from(state)
    if llm.last_tokens:
        budget.used_tokens += llm.last_tokens
    brief = ResearchBrief.model_validate(apply_forced_depth(dump(raw)))
    payload = pythonize(
        {
            "type": "research_brief",
            "title": "Research plan",
            "subtitle": "Review or edit before Kiln starts searching. Confirm the brief, then reason.",
            "query": query,
            "brief": dump(brief),
            "rows": _brief_rows(brief),
        }
    )
    event("briefing_interrupt", goal=brief.goal)
    decision = interrupt(payload)
    if isinstance(decision, str):
        decision = {"action": decision}

    action = (decision or {}).get("action", "start")
    if action == "cancel":
        return {
            "status": "cancelled",
            "brief_confirmed": False,
            "human_decision": decision or {"action": "cancel"},
            "traces": [{"node": "briefing", "action": "cancel"}],
        }

    merged = _merge_brief(brief, (decision or {}).get("brief") or {})
    refined_query = _compose_query(query, merged)
    event("briefing_confirmed", action=action, goal=merged.goal)
    return {
        "brief": _brief_with_contract(refined_query, merged),
        "brief_confirmed": True,
        "query": refined_query,
        "query_type": merged.query_type,
        "status": "researching",
        "human_decision": decision or {"action": "start"},
        "llm_mode": llm.mode,
        "budget": dump(budget),
        "traces": [{"node": "briefing", "action": action, "goal": merged.goal}],
    }


def briefing_node_auto(state: ResearchState) -> dict:
    """Non-interactive briefing for eval / CLI (no interrupt)."""
    query = state["query"]
    if out_of_scope(query):
        return {
            "out_of_scope": True,
            "status": "out_of_scope",
            "brief_confirmed": True,
            "traces": [{"node": "briefing", "decision": "out_of_scope"}],
        }
    brief = ResearchBrief.model_validate(apply_forced_depth(dump(_llm_brief(query) or _heuristic_brief(query))))
    composed = _compose_query(query, brief)
    return {
        "brief": _brief_with_contract(composed, brief),
        "brief_confirmed": True,
        "query": composed,
        "query_type": brief.query_type,
        "status": "researching",
        "traces": [{"node": "briefing", "action": "auto"}],
    }


def _heuristic_brief(query: str) -> ResearchBrief:
    """A brief derived from the question's own shape, with no subject assumptions."""
    qtype = classify_query(query)
    goal = user_goal(query) or query.strip()
    sector_m = SECTOR_RE.search(query)
    stack_m = STACK_RE.search(query)
    subjects = entity_candidates(goal, limit=4)
    sector = sector_m.group(0) if sector_m else (", ".join(subjects[:3]) if subjects else "")
    stack = stack_m.group(0) if stack_m else ""
    mechanism = is_mechanism_query(query)
    comparison = is_comparison_query(query)
    depth = "deep"
    must = must_cover_for(query)
    if mechanism:
        decision = "Explain how it works and where the explanation stops being sourced"
    elif comparison:
        decision = "Compare the named subjects on the dimensions the question implies"
    else:
        decision = _decision_type(qtype)
    hyps = competing_hypotheses(query)
    return ResearchBrief(
        goal=goal,
        query_type=qtype.value,
        sector=sector.title() if sector else "General research",
        geography=stack.upper() if stack else "Not constrained",
        time_horizon="Current",
        decision_type=decision,
        constraints=[
            "Answer the question that was asked, not an adjacent one",
            "Prefer primary sources over commentary for any load-bearing claim",
            "No inventing URLs, figures, or quotations",
        ],
        must_cover=must,
        must_answer=must_answer_for(query),
        sources_priority=[
            "Primary papers, standards, or official documentation",
            "Official source repositories and reference implementations",
            "Independent evaluations and measurements",
            "Secondary write-ups only as pointers",
        ],
        out_of_scope=[
            "Unsourced speculation presented as fact",
            "Marketing claims treated as verified behaviour",
            "Generic templates that do not match what was asked",
        ],
        deliverable=(
            "Cited memo that reconstructs the mechanism and marks what is unverified"
            if mechanism
            else "Cited memo with claims, quotes, confidence, and contradictions"
        ),
        depth=depth,
        assumptions=[
            "The reader wants an evidence-grade answer, not a summary of opinions",
            "Numbers are estimates unless a cited source states them",
            "Both hypotheses stay open until primary evidence forces a qualified lean",
        ],
        hypotheses=hyps,
        subquestions=research_subquestions(query, hyps),
    )


def _resolve_brief(query: str) -> ResearchBrief:
    return _llm_brief(query) or _heuristic_brief(query)


def _llm_brief(query: str) -> ResearchBrief | None:
    if not llm.available:
        return None
    with use_role_model(llm, "briefing"):
        payload = llm.generate_json(
            prompt=(
                f"User question:\n{query}\n\n"
                "Build a research brief the user can edit before searching.\n"
                "Infer the subject area from the question itself; do not assume a domain.\n"
                "'sector' is the topic of this question. 'must_cover' lists what an answer must establish.\n"
                "Always emit two competing hypotheses (H1 conservative/orchestration, H2 capability/model) "
                "and 6-8 falsifiable subquestions, including one that seeks counter-evidence.\n"
                "JSON keys: goal, query_type (factual|comparison|open_research), sector, geography, "
                "time_horizon, decision_type, constraints (list), must_cover (list), sources_priority (list), "
                "out_of_scope (list), deliverable, depth (always deep), assumptions (list), "
                "hypotheses (list of 2 strings), subquestions (list)."
            ),
            system="You prepare editable research briefs for Kiln, a general research agent.",
        )
    if not isinstance(payload, dict):
        return None
    try:
        brief = ResearchBrief.model_validate(payload)
    except Exception:
        return None
    if len(brief.hypotheses) < 2:
        brief.hypotheses = competing_hypotheses(query)
    if not brief.subquestions:
        brief.subquestions = research_subquestions(query, brief.hypotheses)
    brief.depth = effective_depth()
    return brief


def _decision_type(qtype: QueryType) -> str:
    if qtype == QueryType.COMPARISON:
        return "Compare options and trade-offs"
    if qtype == QueryType.OPEN_RESEARCH:
        return "Recommend a decision with caveats"
    return "Answer a factual / systems question"


def _brief_rows(brief: ResearchBrief) -> list[dict]:
    return [
        {"key": "goal", "label": "Research goal", "value": brief.goal, "editable": True, "kind": "text"},
        {"key": "sector", "label": "Stack / topic", "value": brief.sector, "editable": True, "kind": "text"},
        {"key": "geography", "label": "Runtime", "value": brief.geography, "editable": True, "kind": "text"},
        {"key": "time_horizon", "label": "Time horizon", "value": brief.time_horizon, "editable": True, "kind": "text"},
        {"key": "decision_type", "label": "Decision type", "value": brief.decision_type, "editable": True, "kind": "text"},
        {"key": "depth", "label": "Depth", "value": "deep", "editable": False, "kind": "select", "options": ["deep"]},
        {"key": "query_type", "label": "Query class", "value": brief.query_type, "editable": True, "kind": "select", "options": ["factual", "comparison", "open_research"]},
        {"key": "must_cover", "label": "Must cover", "value": brief.must_cover, "editable": True, "kind": "list"},
        {"key": "must_answer", "label": "Must-answer claims", "value": [m.get("label") or m.get("id") for m in (brief.must_answer or [])], "editable": False, "kind": "list"},
        {"key": "constraints", "label": "Constraints", "value": brief.constraints, "editable": True, "kind": "list"},
        {"key": "sources_priority", "label": "Source priority", "value": brief.sources_priority, "editable": True, "kind": "list"},
        {"key": "out_of_scope", "label": "Out of scope", "value": brief.out_of_scope, "editable": True, "kind": "list"},
        {"key": "deliverable", "label": "Deliverable", "value": brief.deliverable, "editable": True, "kind": "text"},
        {"key": "assumptions", "label": "Assumptions", "value": brief.assumptions, "editable": True, "kind": "list"},
        {"key": "hypotheses", "label": "Competing hypotheses", "value": brief.hypotheses, "editable": True, "kind": "list"},
        {"key": "subquestions", "label": "Research subquestions", "value": brief.subquestions, "editable": True, "kind": "list"},
    ]


def _merge_brief(base: ResearchBrief, patch: dict) -> ResearchBrief:
    data = dump(base)
    if isinstance(patch, dict):
        for key, value in patch.items():
            if key in data and value is not None:
                data[key] = value
    try:
        merged = ResearchBrief.model_validate(apply_forced_depth(data))
    except Exception:
        merged = base
        merged.depth = effective_depth()
    else:
        merged.depth = effective_depth()
    return merged


def _compose_query(original: str, brief: ResearchBrief) -> str:
    # Keep the user goal on line 1 so topic classifiers ignore brief metadata.
    goal = (brief.goal or original).strip()
    parts = [
        goal,
        f"Sector: {brief.sector}" if brief.sector else "",
        f"Geography: {brief.geography}" if brief.geography else "",
        f"Horizon: {brief.time_horizon}" if brief.time_horizon else "",
        f"Decision: {brief.decision_type}" if brief.decision_type else "",
        f"Must cover: {'; '.join(brief.must_cover)}" if brief.must_cover else "",
        f"Hypotheses: {'; '.join(brief.hypotheses)}" if brief.hypotheses else "",
        f"Constraints: {'; '.join(brief.constraints)}" if brief.constraints else "",
    ]
    return "\n".join(p for p in parts if p).strip()
