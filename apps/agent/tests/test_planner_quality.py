from app.domain.schema import AgentName, Budget
from app.graph.nodes.enrich import _enrich_cap, _prioritize_enrich_urls
from app.graph.nodes.planner import _cap_sub_queries, _followups_from_prior, _merge_subqueries, _planner_sync, _reserve_calls
from app.domain.adversarial import falsification_queries
from app.domain.decompose import subquestions_for
from app.domain.scholar_query import normalize_plan_subqueries
from app.domain.retrieval_limits import RETRIEVAL_POOL


Q = (
    "How does Punica/LoRAX efficiently serve thousands of LoRA adapters in the same batch "
    "without blowing GPU memory, and what are the kernel-level bottlenecks?"
)


def test_iter1_merges_slot_queries_with_falsification():
    slots = subquestions_for(Q, 8)
    fals = falsification_queries(Q, {"depth": "deep"})
    merged = _merge_subqueries(slots, fals)
    assert len(merged) >= len(slots)
    labels = " ".join(s.question.lower() for s in merged)
    assert "contrary" in labels or "counter" in labels or "benchmark" in labels
    assert any("mechanism" in s.rationale.lower() or "dimension" in s.rationale.lower() for s in merged)


def test_deep_first_pass_reserves_budget_for_critic_loop():
    assert _reserve_calls("deep", 1) == 16


def test_deep_followup_rounds_still_reserve_for_later_rounds():
    """Iterations 2..5 used to get zero reserve — call_cap was the whole
    remaining pool, so a single followup round could spend everything pass 1
    left behind and starve iterations 3+ before after_critic's stagnation
    detector (needs 3 iterations of history) ever got data. Each non-final
    round should still hold something back."""
    assert _reserve_calls("deep", 2, max_iterations=6) > 0
    assert _reserve_calls("deep", 3, max_iterations=6) > 0
    assert _reserve_calls("deep", 5, max_iterations=6) > 0
    # Last allowed iteration has no next round to protect.
    assert _reserve_calls("deep", 6, max_iterations=6) == 0


def test_reserve_pool_spread_lets_a_query_reach_three_iterations():
    """End-to-end budget math: with the old all-or-nothing reserve, a
    single gap-fill round at iteration 2 could exhaust the pool and after_
    critic would stop right after iteration 2 — never giving the 3-
    iteration stagnation detector a chance to run. Simulate worst case
    (each round spends its full call_cap) and confirm iteration 3 still has
    retrieval budget available."""
    pool = RETRIEVAL_POOL["deep"]
    remaining = pool
    for iteration in (1, 2):
        reserve = _reserve_calls("deep", iteration, max_iterations=6)
        call_cap = max(2, remaining - reserve)
        remaining -= call_cap
    assert remaining > 0, "iteration 3 has no budget left to run"


def test_planner_iter1_uses_slot_aligned_followups(monkeypatch):
    monkeypatch.setattr("app.graph.nodes.planner.corpus_available", lambda *_: False)
    state = {
        "query": Q,
        "brief": {"depth": "deep"},
        "budget": Budget(max_iterations=5, max_tool_calls=52).model_dump(mode="json"),
        "followups": [],
    }
    out = _planner_sync(state)
    plan = out["plan"]
    subs = plan.get("sub_queries") or []
    assert len(subs) <= 12
    assert len(subs) >= 3
    rationales = " ".join(s.get("rationale", "") for s in subs).lower()
    assert "dimension" in rationales or "must-answer" in rationales or "cover" in rationales


def test_subquestions_seeds_docs_query_for_named_frameworks():
    """Real memo output described LangGraph/AutoGen's APIs from the writer's
    own training data because none of the retrieved evidence ever mentioned
    them — the cold-start plan never sought their official docs in the first
    place. A question naming specific frameworks must seed a docs-biased
    sub-query for each one, ahead of the generic slot followups, so it isn't
    dropped by subquestions_for's length cap on a tight budget."""
    # Real query: 5 named subjects, with LangGraph/AutoGen/CrewAI listed
    # 3rd-5th — this reproduces the actual bug (a [:3] entity slice covered
    # only "OpenAI"/"Agents"/"Anthropic" and silently dropped the rest).
    q = (
        "Compare representative agent frameworks (e.g., OpenAI Agents, "
        "Anthropic Claude-based agents, LangGraph, AutoGen, and CrewAI) for "
        "multi-agent orchestration."
    )
    subs = subquestions_for(q, remaining_calls=1)
    questions = " | ".join(s.question for s in subs)
    assert "docs" in questions
    for name in ("LangGraph", "AutoGen", "CrewAI"):
        assert name in questions, f"{name} missing from seeded docs queries: {questions}"


def test_entity_docs_queries_survive_plan_deduplication():
    """subquestions_for correctly seeded a docs query per named entity, but
    the planner unconditionally pipes every sub_query through
    normalize_plan_subqueries -> dedupe_subqueries afterward, which drops
    any query sharing >=50% of its tokens with an earlier one. A shared
    boilerplate suffix like "official documentation architecture features"
    made every entity's query a near-duplicate of the first (real bug:
    LangGraph/AutoGen/CrewAI's docs queries were silently deduped away and
    only "OpenAI docs"-flavored ones — whichever was seeded first — ever
    reached search, so live runs kept citing arxiv surveys for framework
    capability claims instead of the frameworks' own documentation)."""
    q = (
        "Compare representative agent frameworks (e.g., OpenAI Agents, "
        "Anthropic Claude-based agents, LangGraph, AutoGen, and CrewAI) for "
        "multi-agent orchestration."
    )
    subs = subquestions_for(q, remaining_calls=8)
    deduped = normalize_plan_subqueries(subs, q)
    questions = " | ".join(s.question for s in deduped)
    for name in ("LangGraph", "AutoGen", "CrewAI"):
        assert name in questions, f"{name} was deduped away: {questions}"


def test_subquestions_finds_named_entities_in_briefing_composed_query():
    """The real, deepest-layer bug: by the time subquestions_for runs, the
    planner's `query` argument is no longer the user's raw question — it's
    briefing._compose_query's blob (goal line 1, then "Sector:/Must cover:/
    Constraints:" metadata lines). A real brief turned "compare OpenAI
    Agents, LangGraph, AutoGen, and CrewAI" into goal text reading "...
    architectural trade-offs of transitioning from single-agent to
    multi-agent systems...", losing every named subject — and
    entity_candidates calls user_goal() internally, which stops at the
    first metadata line, so scanning the goal alone found zero entities
    even though they survived verbatim in the real "Constraints:" line.
    This is the exact composed blob from that live run (traced via
    docker logs), confirming the fix reads metadata content, not just the
    (already-abstracted) goal line."""
    composed_query = (
        "Evaluate the empirical reliability, efficiency, and architectural "
        "trade-offs of transitioning from single-agent to multi-agent LLM "
        "systems for long-horizon research tasks, specifically isolating "
        "structural architectural gains from inference-time compute "
        "scaling.\n"
        "Sector: Artificial Intelligence / Autonomous LLM Agents\n"
        "Geography: Global\n"
        "Horizon: 2023-2025\n"
        "Decision: Architectural selection and system design for AI research workflows\n"
        "Must cover: Comparative analysis across six key dimensions: task "
        "decomposition, planning, memory management, tool orchestration, "
        "inter-agent communication, and error propagation.; Empirical "
        "performance on benchmarks (e.g., GAIA, SWE-bench, WebArena) across "
        "task success rate, factuality, token consumption, latency, and "
        "cost per successful task.\n"
        "Constraints: Must compare compute-matched baselines (equal token "
        "and inference budget).; Must cover representative frameworks: "
        "OpenAI Agents, Anthropic Claude-based agents, LangGraph, AutoGen, "
        "and CrewAI.; Must evaluate across web research, code generation, "
        "and multi-hop reasoning tasks."
    )
    subs = subquestions_for(composed_query, remaining_calls=28)
    questions = " | ".join(s.question for s in subs)
    for name in ("OpenAI", "Anthropic", "LangGraph", "AutoGen", "CrewAI"):
        assert name in questions, f"{name} missing: {questions}"


def test_followups_from_prior_reuses_derived_search_query(monkeypatch):
    """Stored slots drop the LLM-crafted search query at save time
    (`_slim_slots` keeps only id/label/status/critical), so a naive rebuild
    used to mash the whole goal sentence together with the slot label into
    one long keyword-soup string. Real run: that string got re-submitted
    identically for 3 straight iterations and returned zero new evidence.
    Re-deriving slots for the same goal must recover the real query instead."""
    monkeypatch.setattr(
        "app.graph.nodes.planner.derive_slots",
        lambda goal: [
            {"id": "frameworks", "label": "Named framework comparison", "followup": "LangGraph AutoGen CrewAI benchmark comparison"},
            {"id": "quantitative", "label": "Measured numbers, benchmarks, latency, or cost", "followup": "GAIA SWE-bench multi-agent latency cost benchmark"},
        ],
    )
    record = {
        "goal": "Evaluate single-agent vs multi-agent LLM architectures for long-horizon tasks.",
        "slots": [
            {"id": "frameworks", "label": "Named framework comparison", "status": "open"},
            {"id": "quantitative", "label": "Measured numbers, benchmarks, latency, or cost", "status": "open"},
        ],
    }
    out = _followups_from_prior(record, [])
    questions = [s.question for s in out]
    assert "LangGraph AutoGen CrewAI benchmark comparison" in questions
    assert "GAIA SWE-bench multi-agent latency cost benchmark" in questions
    assert not any(q.startswith("Evaluate single-agent vs multi-agent") for q in questions)


def test_followups_from_prior_falls_back_when_no_derived_query(monkeypatch):
    monkeypatch.setattr("app.graph.nodes.planner.derive_slots", lambda goal: [])
    record = {
        "goal": "Evaluate single-agent vs multi-agent LLM architectures.",
        "slots": [{"id": "frameworks", "label": "Named framework comparison", "status": "open"}],
    }
    out = _followups_from_prior(record, [])
    assert out[0].question.startswith("Evaluate single-agent vs multi-agent")


def test_enrich_cap_limits_first_pass():
    assert _enrich_cap("deep", 1) == 18
    assert _enrich_cap("deep", 2) == 24


def test_enrich_prioritizes_primary_urls():
    evidence = [
        {"url": "https://medium.com/x", "snippet": "blog"},
        {"url": "https://arxiv.org/abs/2401.1", "snippet": "paper"},
        {"url": "https://github.com/org/repo", "snippet": "code"},
    ]
    ordered = _prioritize_enrich_urls(evidence, [e["url"] for e in evidence])
    assert ordered[0].startswith("https://arxiv.org")
    assert "github.com" in ordered[1]


def test_enrich_prioritizes_framework_official_docs_over_generic_blog():
    """AutoGen's real docs live on microsoft.github.io, not a bare github.com
    repo URL — before this fix that host matched none of the doc markers and
    ranked no higher than a random blog."""
    evidence = [
        {"url": "https://medium.com/x", "snippet": "blog"},
        {
            "url": "https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html",
            "snippet": "official docs",
        },
    ]
    ordered = _prioritize_enrich_urls(evidence, [e["url"] for e in evidence])
    assert ordered[0].startswith("https://microsoft.github.io")


def _sq(agent: AgentName, n: int) -> list:
    from app.domain.schema import SubQuery

    return [SubQuery(agent=agent, question=f"q{i}", rationale="") for i in range(n)]


def test_cap_sub_queries_accounts_for_scholar_double_call_cost():
    """A scholar sub-query can cost 2 external calls (OpenAlex + Semantic
    Scholar augment, which fires whenever OpenAlex returns <5 results —
    common, not rare). Capping by raw sub-query count let a single planner
    pass burn past DEEP_RESERVE_CALLS, the budget iteration 1 is supposed to
    leave for the critic followup loop."""
    subs = _sq(AgentName.SCHOLAR, 6)
    capped = _cap_sub_queries(subs, "deep", remaining_calls=8)
    assert sum(2 if s.agent == AgentName.SCHOLAR else 1 for s in capped) <= 8


def test_cap_sub_queries_still_caps_by_count_for_cheap_agents():
    subs = _sq(AgentName.SEARCH, 20)
    capped = _cap_sub_queries(subs, "deep", remaining_calls=28)
    assert len(capped) <= 12  # PLAN_SUBQUERY_CAPS["deep"]


def test_cap_sub_queries_never_returns_empty():
    subs = _sq(AgentName.SCHOLAR, 3)
    capped = _cap_sub_queries(subs, "deep", remaining_calls=1)
    assert len(capped) >= 1
