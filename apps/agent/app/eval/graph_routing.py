"""Eval: graph routing scenarios without live LLM (critic + builder edges)."""

from __future__ import annotations

from unittest.mock import patch

from app.domain.coverage_gate import GATE_INSUFFICIENT_BUDGET, GATE_INSUFFICIENT_COVERAGE
from app.domain.research_depth import configure_budget_pools
from app.domain.schema import Budget
from app.graph.builder import after_critic
from app.graph.nodes.critic import critic_node

_SLOTS = [
    {"id": "mechanism", "label": "Mechanism", "critical": True, "status": "open", "patterns": [r"batch"]},
    {"id": "latency", "label": "Latency", "critical": True, "status": "open", "patterns": [r"latency"]},
]


def _evidence():
    return [
        {
            "id": "e1",
            "title": "vLLM",
            "url": "https://arxiv.org/abs/2301.00001",
            "snippet": "continuous batching",
            "quote": "continuous batching",
            "tier": "peer_reviewed",
            "credibility": 0.8,
        }
    ]


def score_graph_routing() -> dict:
    rows = []
    q = "How does vLLM batch requests?"

    with patch("app.graph.nodes.critic._llm_critic", return_value=None):
        budget_loop = configure_budget_pools(Budget(max_iterations=3, iterations=1), "deep")
        out_loop = critic_node(
            {
                "query": q,
                "retrieved": _evidence(),
                "brief": {"must_answer": _SLOTS, "depth": "deep"},
                "budget": budget_loop.model_dump(),
            }
        )
        rows.append(
            {
                "id": "critic_loops_with_budget",
                "pass": out_loop["critic"]["gate_reason"] == GATE_INSUFFICIENT_COVERAGE
                and bool(out_loop["followups"]),
            }
        )

        budget_done = configure_budget_pools(
            Budget(max_iterations=2, iterations=2, used_retrieval_calls=28), "deep"
        )
        out_done = critic_node(
            {
                "query": q,
                "retrieved": _evidence(),
                "brief": {"must_answer": _SLOTS, "depth": "deep"},
                "budget": budget_done.model_dump(),
            }
        )
        rows.append(
            {
                "id": "critic_budget_exhausted_gate",
                "pass": out_done["critic"]["gate_reason"] == GATE_INSUFFICIENT_BUDGET,
            }
        )

        route = after_critic(
            {
                "budget": budget_done.model_dump(),
                "critic": out_done["critic"],
            }
        )
        rows.append({"id": "after_critic_hitl_on_exhausted", "pass": route == "hitl"})

    passed = sum(1 for r in rows if r["pass"])
    return {"passed": passed, "total": len(rows), "rows": rows}


if __name__ == "__main__":
    import json

    print(json.dumps(score_graph_routing(), indent=2))
