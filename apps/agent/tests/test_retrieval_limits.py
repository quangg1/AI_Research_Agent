from app.domain.retrieval_limits import (
    API_RESULTS_PER_QUERY,
    DEEP_MAX_TOOL_CALLS,
    DEEP_RESERVE_CALLS,
    ENRICH_POOL,
    ENRICH_FETCH_CAP,
    RANK_NO_PRIMARY_CAP,
    RANK_PRIMARY_CAP,
    RETRIEVAL_POOL,
    SEARCH_QUERY_CAPS,
)
from app.graph.nodes.search import _rank_and_filter
from app.tools.fetch import is_fetchable


def test_search_query_caps_deep():
    assert SEARCH_QUERY_CAPS["deep"] == 10


def test_rank_and_filter_keeps_more_primary_hits():
    hits = [
        {"url": f"https://arxiv.org/abs/2401.{i}", "tier": "peer_reviewed", "id": f"e{i}"}
        for i in range(12)
    ]
    hits += [{"url": "https://example.com/blog/post", "tier": "news_analysis", "id": "blog"}]
    ranked = _rank_and_filter(hits)
    assert len(ranked) >= 10
    assert len(ranked) <= RANK_PRIMARY_CAP


def test_rank_and_filter_no_primary_keeps_ten():
    hits = [
        {"url": f"https://example.com/article-{i}", "tier": "news_analysis", "id": f"e{i}"}
        for i in range(12)
    ]
    ranked = _rank_and_filter(hits)
    assert len(ranked) == RANK_NO_PRIMARY_CAP


def test_enrich_fetchable_allows_blog_urls():
    assert is_fetchable("https://medium.com/@user/some-article")
    assert not is_fetchable("https://medium.com/")


def test_deep_budget_and_reserve():
    assert DEEP_MAX_TOOL_CALLS >= 48
    assert DEEP_RESERVE_CALLS == 10
    assert DEEP_MAX_TOOL_CALLS == RETRIEVAL_POOL["deep"] + ENRICH_POOL["deep"]
    assert API_RESULTS_PER_QUERY == 15
    assert ENRICH_FETCH_CAP["deep"] == (18, 24)
