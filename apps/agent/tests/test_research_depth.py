from app.domain.research_depth import apply_forced_depth, configure_budget_pools, effective_depth
from app.domain.schema import Budget
from app.domain.retrieval_limits import ENRICH_POOL, RETRIEVAL_POOL


def test_effective_depth_always_deep():
    assert effective_depth({"depth": "standard"}) == "deep"
    assert effective_depth(None) == "deep"
    assert apply_forced_depth({"depth": "quick"})["depth"] == "deep"


def test_configure_split_pools_deep():
    budget = configure_budget_pools(Budget(), "deep")
    assert budget.max_retrieval_calls == RETRIEVAL_POOL["deep"]
    assert budget.max_enrich_calls == ENRICH_POOL["deep"]
    assert budget.max_tool_calls == RETRIEVAL_POOL["deep"] + ENRICH_POOL["deep"]


def test_enrich_does_not_consume_retrieval_pool():
    budget = configure_budget_pools(Budget(), "deep")
    budget.used_retrieval_calls = RETRIEVAL_POOL["deep"]
    budget.sync_totals()
    assert budget.remaining_retrieval_calls == 0
    assert budget.remaining_enrich_calls == ENRICH_POOL["deep"]
