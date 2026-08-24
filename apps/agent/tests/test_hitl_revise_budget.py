from app.domain.adversarial import extract_quantitative_rows
from app.graph.builder import after_hitl
from app.graph.nodes.hitl import REVISE_EXTRA_TOOL_CALLS, hitl_node
from app.domain.schema import Budget


def test_benchmark_name_extracted_or_flagged():
    rows = extract_quantitative_rows(
        [
            {
                "title": "AgentOrchestra on GAIA",
                "url": "https://arxiv.org/abs/2601.1",
                "quote": "On GAIA the system reaches 92.45% overall task completion.",
                "tier": "peer_reviewed",
            },
            {
                "title": "Mystery paper",
                "url": "https://arxiv.org/abs/2601.2",
                "quote": "We observe a 4.8% success rate on hard subsets.",
                "tier": "peer_reviewed",
            },
        ],
        [
            {"n": 1, "url": "https://arxiv.org/abs/2601.1"},
            {"n": 2, "url": "https://arxiv.org/abs/2601.2"},
        ],
    )
    by_n = {r["n"]: r for r in rows}
    assert by_n[1]["benchmark_name"].lower() == "gaia"
    assert by_n[1].get("warning") in ("", None)
    assert by_n[2]["benchmark_name"] == "unverified benchmark"
    assert by_n[2]["warning"] == "Unverified Benchmark"


def test_after_hitl_revise_even_when_budget_exhausted():
    state = {
        "human_decision": {"action": "revise"},
        "budget": Budget(
            iterations=3, max_iterations=3, used_tool_calls=12, max_tool_calls=12
        ).model_dump(),
    }
    assert after_hitl(state) == "planner"


def test_revise_grants_budget_headroom(monkeypatch):
    from app.graph.nodes import hitl as hitl_mod

    monkeypatch.setattr(hitl_mod, "interrupt", lambda payload: {"action": "revise", "extra_questions": []})
    out = hitl_node(
        {
            "query": "multi-agent vs single-agent",
            "retrieved": [],
            "budget": Budget(
                iterations=3, max_iterations=3, used_tool_calls=12, max_tool_calls=12
            ).model_dump(),
        }
    )
    budget = out["budget"]
    assert budget["max_iterations"] >= 4
    assert budget["max_tool_calls"] >= 12 + REVISE_EXTRA_TOOL_CALLS
    assert out["status"] == "revising"
