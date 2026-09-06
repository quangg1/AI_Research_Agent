from __future__ import annotations

import asyncio

from app.domain.coverage import must_answer_for
from app.domain.adversarial import falsification_queries
from app.domain.decompose import derive_slots, subquestions_for
from app.domain.knowledge import lookup, partial_evidence, seed_evidence
from app.domain.routing_policy import heuristic_plan, is_learning_query, out_of_scope
from app.domain.research_depth import apply_forced_depth, configure_budget_pools, effective_depth, showcase_reserve_calls
from app.domain.retrieval_limits import PLAN_SUBQUERY_CAPS
from app.domain.scholar_query import normalize_plan_subqueries
from app.domain.research_intent import is_mechanism_query, user_goal
from app.domain.schema import AgentName, Budget, Plan, QueryType, SubQuery
from app.graph.serde import dump
from app.graph.state import ResearchState, budget_from
from app.llm.client import llm
from app.llm.roles import use_role_model
from app.observability.logging import event
from app.retrieval.chunk import load_corpus
from app.retrieval.hybrid import corpus_is_relevant
from app.retrieval.store import corpus_available, get_store_documents


async def planner_node(state: ResearchState) -> dict:
    return await asyncio.to_thread(_planner_sync, state)


def _planner_sync(state: ResearchState) -> dict:
    query = state["query"]
    budget = budget_from(state)
    budget.iterations += 1
    followups = [SubQuery.model_validate(x) for x in (state.get("followups") or [])]
    brief = apply_forced_depth(state.get("brief") or {})
    depth = effective_depth(brief)
    if budget.iterations == 1:
        configure_budget_pools(budget, depth)
        budget.max_iterations = max(budget.max_iterations, 6)

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
        budget.max_retrieval_calls = max(2, min(budget.max_retrieval_calls, 6))
        budget.sync_totals()
        reuse_patch = {
            "reuse_mode": "augment",
            "reuse_similarity": hit.similarity,
            "reuse_age_days": hit.age_days,
            "prior_knowledge": hit.record,
            "prior_knowledge_id": hit.record.get("id"),
            "evidence": partial_evidence(hit.record),
        }
        followups = _followups_from_prior(hit.record, followups)
        event("planner_knowledge_augment", similarity=hit.similarity, knowledge_id=hit.record.get("id"))

    if budget.iterations == 1 and not followups:
        slot_queries = subquestions_for(query, budget.remaining_calls)
        fals = falsification_queries(query, brief)
        followups = _merge_subqueries(slot_queries, fals)

    org_id = state.get("org_id") or None
    has_corpus = corpus_available(org_id)
    learning = is_learning_query(query)
    mechanism = is_mechanism_query(query)
    store_docs = get_store_documents(org_id)
    corpus_relevant = bool(has_corpus) and corpus_is_relevant(
        user_goal(query), store_docs or load_corpus()
    )
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
    reserve = _reserve_calls(depth, budget.iterations)
    call_cap = max(2, budget.remaining_retrieval_calls - reserve) if budget.iterations == 1 else budget.remaining_retrieval_calls
    plan.sub_queries = _cap_sub_queries(plan.sub_queries, depth, call_cap)
    plan.sub_queries = normalize_plan_subqueries(plan.sub_queries, query)

    event(
        "planner",
        query_type=plan.query_type.value,
        agents=[a.value for a in plan.agents_to_run],
        iteration=budget.iterations,
        remaining_calls=budget.remaining_retrieval_calls,
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
        "plan_confirmed": False,
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


def _cap_sub_queries(subs: list[SubQuery], depth: str, remaining_calls: int) -> list[SubQuery]:
    cap = min(PLAN_SUBQUERY_CAPS.get(depth, 8), max(1, remaining_calls))
    return list(subs)[:cap]


def _reserve_calls(depth: str, iteration: int) -> int:
    """Keep budget for critic-driven gap loops instead of burning it all on pass 1."""
    if iteration != 1:
        return 0
    return showcase_reserve_calls(depth, iteration)


def _merge_subqueries(*groups: list[SubQuery]) -> list[SubQuery]:
    seen: set[str] = set()
    out: list[SubQuery] = []
    for group in groups:
        for sub in group:
            key = sub.question.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(sub)
    return out


def _knowledge_hit(state: ResearchState, budget: Budget, followups: list[SubQuery]):
    """Closest stored answer, only considered on the first pass of a new question."""
    if budget.iterations > 1 or followups or state.get("reuse_mode"):
        return None
    try:
        return lookup(state.get("query") or "", org_id=state.get("org_id") or None)
    except Exception as exc:
        event("knowledge_lookup_failed", error=str(exc)[:160])
        return None


def _followups_from_prior(record: dict, followups: list[SubQuery]) -> list[SubQuery]:
    """Target the dimensions the stored answer never nailed down.

    Stored slots keep only id/label/status/critical (see `_slim_slots`) — the
    short, LLM-crafted search query each slot was built with isn't persisted.
    Re-deriving slots for the same goal text hits `derive_slots`' cache (it
    was already computed once for this exact goal) and gets that query back
    for free, instead of falling back to `goal + label` mashed together into
    one long, unnatural string that search/scholar treat as a poor query.
    """
    if followups:
        return followups
    goal = record.get("goal") or ""
    fresh_by_id = {s["id"]: s.get("followup") for s in derive_slots(goal)} if goal else {}
    out: list[SubQuery] = []
    for slot in record.get("slots") or []:
        if slot.get("status") == "covered":
            continue
        label = slot.get("label") or slot.get("id")
        if not label:
            continue
        question = fresh_by_id.get(slot.get("id")) or f"{goal} {label}"
        out.append(
            SubQuery(
                agent=AgentName.SEARCH,
                question=question[:200],
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
    must_answer = [
        {"id": s.get("id"), "label": s.get("label"), "critical": s.get("critical")}
        for s in must_answer_for(query)
    ]
    with use_role_model(llm, "planner"):
        payload = llm.generate_json(
            prompt=(
                f"User question (goal):\n{goal}\n\n"
                f"Full brief blob (metadata only):\n{query}\n\n"
                f"Must-answer dimensions (each needs a dedicated sub_query):\n{must_answer}\n\n"
                f"Follow-up questions from critic: {[dump(f) for f in followups]}\n"
                f"Remaining tool calls: {budget.remaining_calls}. Remaining tokens: {budget.remaining_tokens}.\n"
                f"{corpus_line}\n"
                "Classify as factual | comparison | open_research.\n"
                "Pick only the agents needed from search, scholar, docs.\n"
                "Emit multiple sub_queries that each chase a different claim — not copies of the same string.\n"
                "Every critical must-answer dimension above must have at least one sub_query targeting it.\n"
                "At least one sub_query must seek counter-evidence or a result that would falsify the convenient thesis.\n"
                "At least one sub_query must seek a measured number (benchmark, N, success rate, delta).\n"
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
