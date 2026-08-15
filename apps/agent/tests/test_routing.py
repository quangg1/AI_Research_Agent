from app.domain.routing_policy import agents_for, classify_query, heuristic_plan, is_learning_query, out_of_scope
from app.domain.schema import AgentName, Budget, QueryType
from app.graph.builder import after_critic, after_planner


def test_factual_does_not_fan_out_all_three():
    q = "What is continuous batching in vLLM and how does it affect time-to-first-token?"
    assert classify_query(q) is QueryType.FACTUAL
    agents = set(agents_for(QueryType.FACTUAL, remaining_calls=12))
    assert AgentName.SCHOLAR not in agents
    assert AgentName.DOCS in agents


def test_comparison_uses_all_agents():
    q = "Compare a vector-only RAG stack vs BM25 plus a cross-encoder reranker."
    assert classify_query(q) is QueryType.COMPARISON
    assert set(agents_for(QueryType.COMPARISON, 12)) == {AgentName.DOCS, AgentName.SEARCH, AgentName.SCHOLAR}


def test_open_research():
    q = "Should we fine-tune a 8B model on weekly runbooks, or use RAG over the same docs?"
    assert classify_query(q) is QueryType.OPEN_RESEARCH


def test_budget_collapses_agents():
    assert agents_for(QueryType.OPEN_RESEARCH, remaining_calls=1) == [AgentName.DOCS]


def test_no_corpus_uses_live_search_not_docs():
    assert AgentName.SEARCH in agents_for(QueryType.FACTUAL, 12, has_corpus=False)
    assert AgentName.DOCS not in agents_for(QueryType.FACTUAL, 12, has_corpus=False)
    assert agents_for(QueryType.OPEN_RESEARCH, remaining_calls=1, has_corpus=False) == [AgentName.SEARCH]
    plan = heuristic_plan("What is continuous batching in vLLM?", 8, has_corpus=False)
    assert AgentName.SEARCH in plan.agents_to_run
    assert AgentName.DOCS not in plan.agents_to_run


def test_out_of_scope():
    assert out_of_scope("Build me a NestJS chatbot UI with a Kaggle sentiment dataset.")


def test_after_planner_out_of_scope_goes_to_report():
    assert after_planner({"out_of_scope": True, "agents_to_run": []}) == "report"


def test_critic_loops_when_insufficient_and_budget_left():
    state = {
        "budget": Budget(max_iterations=3, iterations=1, max_tool_calls=12, used_tool_calls=3).model_dump(),
        "critic": {"status": "insufficient", "followup_queries": [{"agent": "docs", "question": "x"}]},
        "followups": [{"agent": "docs", "question": "x"}],
    }
    assert after_critic(state) == "planner"


def test_critic_stops_when_budget_exhausted():
    state = {
        "budget": Budget(max_iterations=2, iterations=2, max_tool_calls=4, used_tool_calls=4).model_dump(),
        "critic": {"status": "insufficient", "followup_queries": [{"agent": "search", "question": "x"}]},
    }
    assert after_critic(state) == "hitl"


def test_critic_loops_on_contradiction_with_followups():
    state = {
        "budget": Budget(max_iterations=3, iterations=1, max_tool_calls=12, used_tool_calls=3).model_dump(),
        "critic": {"status": "contradicted", "followup_queries": [{"agent": "docs", "question": "resolve vector vs bm25"}]},
        "followups": [{"agent": "docs", "question": "resolve vector vs bm25"}],
    }
    assert after_critic(state) == "planner"


def test_learning_path_is_not_a_corpus_comparison():
    q = "Design an optimized, high-yield learning path for mastering efficient AI engineering, focusing on LLM systems, RAG, and fine-tuning vs retrieval."
    assert is_learning_query(q)
    assert classify_query(q) is QueryType.OPEN_RESEARCH
    agents = agents_for(QueryType.OPEN_RESEARCH, 12, live_first=True)
    assert agents[0] is AgentName.SEARCH
    assert AgentName.DOCS not in agents[:2]
    plan = heuristic_plan(q, 12)
    assert plan.agents_to_run[0] is AgentName.SEARCH
    assert AgentName.DOCS not in plan.agents_to_run[:2]
    plan = heuristic_plan("What is continuous batching in vLLM?", 8)
    assert plan.query_type is QueryType.FACTUAL
    assert AgentName.DOCS in plan.agents_to_run


def test_off_corpus_plan_uses_live_search_not_docs():
    q = "How does Punica/LoRAX batch multiple LoRA adapters in one forward pass?"
    plan = heuristic_plan(q, 12, live_first=True)
    assert plan.agents_to_run[0] is AgentName.SEARCH
    assert AgentName.DOCS not in plan.agents_to_run
    assert all("RAG evaluation survey" not in s.question for s in plan.sub_queries)
