from app.domain.schema import Budget
from app.graph.nodes import search
from app.graph.nodes.collector import collector_node
from app.tools import cache


def test_search_cached_hit_reports_zero_external_calls(monkeypatch):
    cache._STORE.clear()
    monkeypatch.setattr(search.settings, "tavily_api_key", "")
    monkeypatch.setattr(
        search,
        "_ddg",
        lambda query: [
            search._to_evidence(
                "Useful source",
                "https://example.org/source",
                f"Evidence for {query}",
                search.AgentName.SEARCH,
            )
        ],
    )
    state = {
        "query": "unique budget accounting query",
        "agents_to_run": ["search"],
        "budget": Budget().model_dump(),
    }

    first = search.search_node(state)
    second = search.search_node(state)

    assert first["traces"][0]["external_calls"] == 1
    assert second["traces"][0]["external_calls"] == 0


def test_collector_charges_latest_external_calls_only():
    state = {
        "query": "test",
        "evidence": [],
        "agents_to_run": ["search", "scholar", "docs", "enrich"],
        "budget": Budget(used_tool_calls=2).model_dump(),
        "traces": [
            {"node": "search", "external_calls": 5},
            {"node": "search", "external_calls": 0},
            {"node": "scholar", "external_calls": 2},
            {"node": "docs", "n": 3},
        ],
    }

    result = collector_node(state)

    assert result["budget"]["used_tool_calls"] == 4
    assert result["traces"][0]["agents_ran"] == ["search", "scholar", "docs"]


def test_collector_falls_back_to_planned_tool_agents():
    state = {
        "query": "test",
        "evidence": [],
        "agents_to_run": ["search", "docs", "enrich"],
        "budget": Budget().model_dump(),
        "traces": [{"node": "docs", "n": 2}],
    }

    result = collector_node(state)

    assert result["budget"]["used_tool_calls"] == 2
