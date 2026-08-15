from __future__ import annotations

import asyncio

from app.domain.knowledge import lookup, seed_evidence
from app.domain.routing_policy import heuristic_plan, is_learning_query, out_of_scope
from app.domain.research_intent import is_mechanism_query, user_goal
from app.domain.schema import AgentName, Budget, Plan, QueryType, SubQuery
from app.graph.serde import dump
from app.graph.state import ResearchState, budget_from
from app.llm.client import llm
from app.observability.logging import event
from app.retrieval.chunk import load_corpus
from app.retrieval.hybrid import corpus_is_relevant
from app.retrieval.store import corpus_available


async def planner_node(state: ResearchState) -> dict:
    return await asyncio.to_thread(_planner_sync, state)


def _planner_sync(state: ResearchState) -> dict:
    query = state["query"]
    budget = budget_from(state)
    budget.iterations += 1
    followups = [SubQuery.model_validate(x) for x in (state.get("followups") or [])]
    brief = state.get("brief") or {}
    depth = (brief.get("depth") or "standard").lower()
    if budget.iterations == 1:
        if depth == "quick":
            budget.max_iterations = min(budget.max_iterations, 1)
            budget.max_tool_calls = min(budget.max_tool_calls, 6)
        elif depth == "deep":
            budget.max_iterations = max(budget.max_iterations, 3)
            budget.max_tool_calls = max(budget.max_tool_calls, 12)

    if out_of_scope(query) and budget.iterations == 1 and not followups:
        event("planner_out_of_scope", query=query)
        return {
            "out_of_scope": True,
            "status": "out_of_scope",
            "budget": dump(budget),
            "plan": dump(heuristic_plan(query, budget.remaining_calls)),
            "agents_to_run": [],
            "llm_mode": llm.mode,
            "traces": [{"node": "planner", "decision": "out_of_scope"}],
        }

    hit = _knowledge_hit(state, budget, followups)
    if hit is not None and hit.mode == "cached":
        event("planner_knowledge_hit", similarity=hit.similarity, knowledge_id=hit.record.get("id"))
        return {
            "reuse_mode": "cached",
            "reuse_similarity": hit.similarity,
            "reuse_age_days": hit.age_days,
            "prior_knowledge": hit.record,
            "prior_knowledge_id": hit.record.get("id"),
            "agents_to_run": [],
            "plan": dump(heuristic_plan(query, 0)),
            "budget": dump(budget),
            "status": "researching",
            "llm_mode": "knowledge_reuse",
            "traces": [
                {
                    "node": "planner",
                    "decision": "knowledge_reuse",
                    "similarity": hit.similarity,
                    "knowledge_id": hit.record.get("id"),
                }
            ],
        }

    # Any path that reaches real research must clear a stale "cached" flag, or the
    # graph would short-circuit to the stored memo again on the next pass.
    reuse_patch: dict = {"reuse_mode": ""}
    if hit is not None:
        # Close-but-not-identical question: keep the prior sources and spend the
        # remaining budget only on what the stored answer left open.
        budget.max_tool_calls = max(2, min(budget.max_tool_calls, 6))
        reuse_patch = {
            "reuse_mode": "augment",
            "reuse_similarity": hit.similarity,
            "reuse_age_days": hit.age_days,
            "prior_knowledge": hit.record,
            "prior_knowledge_id": hit.record.get("id"),
            "evidence": seed_evidence(hit.record),
        }
        followups = _followups_from_prior(hit.record, followups)
        event("planner_knowledge_augment", similarity=hit.similarity, knowledge_id=hit.record.get("id"))

    has_corpus = corpus_available()
    learning = is_learning_query(query)
    mechanism = is_mechanism_query(query)
    corpus_relevant = bool(has_corpus) and corpus_is_relevant(user_goal(query), load_corpus())
    live_first = learning or mechanism or not corpus_relevant
    plan = _llm_plan(query, budget, followups, has_corpus, live_first, mechanism) or heuristic_plan(
        query,
        budget.remaining_calls,
        followups,
        has_corpus=has_corpus,
        live_first=live_first,
    )
    if llm.last_tokens:
        budget.used_tokens += llm.last_tokens
    if live_first:
        names = [a if isinstance(a, AgentName) else AgentName(a) for a in plan.agents_to_run]
        names = [a for a in names if a != AgentName.DOCS]
        if AgentName.SEARCH not in names:
            names = [AgentName.SEARCH, *names]
        if AgentName.SCHOLAR not in names and budget.remaining_calls > 1:
            names.append(AgentName.SCHOLAR)
        plan.agents_to_run = names[: max(1, budget.remaining_calls)] or [AgentName.SEARCH]
        if learning:
            plan.query_type = QueryType.OPEN_RESEARCH
    if not has_corpus:
        names = [a if isinstance(a, AgentName) else AgentName(a) for a in plan.agents_to_run]
        names = [a for a in names if a != AgentName.DOCS]
        if AgentName.SEARCH not in names:
            names = [AgentName.SEARCH, *names]
        plan.agents_to_run = names[: max(1, budget.remaining_calls)] or [AgentName.SEARCH]
    if budget.remaining_calls <= 0:
        plan.agents_to_run = []
        plan.sub_queries = []

    event(
        "planner",
        query_type=plan.query_type.value,
        agents=[a.value for a in plan.agents_to_run],
        iteration=budget.iterations,
        remaining_calls=budget.remaining_calls,
    )
    return {
        "query_type": plan.query_type.value,
        "plan": dump(plan),
        "agents_to_run": [a.value for a in plan.agents_to_run],
        "budget": dump(budget),
        "out_of_scope": False,
        "status": "researching",
        "followups": [],
        "llm_mode": llm.mode,
        **reuse_patch,
        "traces": [
            {
                "node": "planner",
                "query_type": plan.query_type.value,
                "agents": [a.value for a in plan.agents_to_run],
                "iteration": budget.iterations,
                "reuse": reuse_patch.get("reuse_mode") or "none",
            }
        ],
    }


def _knowledge_hit(state: ResearchState, budget: Budget, followups: list[SubQuery]):
    """Closest stored answer, only considered on the first pass of a new question."""
    if budget.iterations > 1 or followups or state.get("reuse_mode"):
        return None
    try:
        return lookup(state.get("query") or "")
    except Exception as exc:
        event("knowledge_lookup_failed", error=str(exc)[:160])
        return None


def _followups_from_prior(record: dict, followups: list[SubQuery]) -> list[SubQuery]:
    """Target the dimensions the stored answer never nailed down."""
    if followups:
        return followups
    out: list[SubQuery] = []
    goal = record.get("goal") or ""
    for slot in record.get("slots") or []:
        if slot.get("status") == "covered":
            continue
        label = slot.get("label") or slot.get("id")
        if not label:
            continue
        out.append(
            SubQuery(
                agent=AgentName.SEARCH,
                question=f"{goal} {label}"[:200],
                rationale=f"Prior memo left this open: {label}",
            )
        )
        if len(out) >= 3:
            break
    return out


def _llm_plan(
    query: str,
    budget: Budget,
    followups: list[SubQuery],
    has_corpus: bool = True,
    live_first: bool = False,
    mechanism: bool = False,
) -> Plan | None:
    if not llm.available:
        return None
    goal = user_goal(query)
    corpus_line = (
        "Local curated corpus is available for the docs agent."
        if has_corpus
        else (
            "Local curated corpus is EMPTY. Do not pick docs as the only agent. "
            "Use search and scholar so citations come from live URLs (arxiv abs, vendor docs, DOI, GitHub)."
        )
    )
    if live_first:
        corpus_line += (
            " The local corpus does NOT cover this question (or it is learning/mechanism). "
            "Do not pick docs. Prefer search + scholar. "
            "Every citation URL must come from live retrieval, not internal Kiln notes."
        )
    if mechanism:
        corpus_line += (
            " MECHANISM question: decompose into sub_queries that seek "
            "(1) math of batched LoRA, (2) segment/adapter indexing, (3) SGMV/CUDA/kernel, "
            "(4) memory layout of A/B, (5) rank heterogeneity limits, (6) system comparison if multiple names. "
            "Prefer arXiv + GitHub over Medium/TowardsAI."
        )
    payload = llm.generate_json(
        prompt=(
            f"User question (goal):\n{goal}\n\n"
            f"Full brief blob (metadata only):\n{query}\n\n"
            f"Follow-up questions from critic: {[dump(f) for f in followups]}\n"
            f"Remaining tool calls: {budget.remaining_calls}. Remaining tokens: {budget.remaining_tokens}.\n"
            f"{corpus_line}\n"
            "Classify as factual | comparison | open_research.\n"
            "Pick only the agents needed from search, scholar, docs.\n"
            "Emit multiple sub_queries that each chase a different claim — not copies of the same string.\n"
            "Stay inside applied AI / LLM systems: serving, RAG, agents, eval, multi-LoRA kernels.\n"
            "JSON keys: query_type, goal, agents_to_run, assumptions, stop_conditions, "
            "sub_queries (list of {agent, question, rationale})."
        ),
        system="You are the planner for Kiln, an LLM-systems research agent. Prefer claim-driven subquestions.",
    )
    if not isinstance(payload, dict):
        return None
    try:
        if followups:
            payload.setdefault("sub_queries", [])
            payload["sub_queries"] = [dump(f) for f in followups] + payload.get("sub_queries", [])
        plan = Plan.model_validate(payload)
        plan.agents_to_run = plan.agents_to_run[: max(1, budget.remaining_calls)]
        return plan
    except Exception:
        return None
