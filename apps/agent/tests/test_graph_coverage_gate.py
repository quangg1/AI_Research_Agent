"""Graph routing + coverage gate integration tests (no live LLM)."""

from __future__ import annotations

import pytest

from app.domain.coverage_gate import (
    GATE_INSUFFICIENT_BUDGET,
    GATE_INSUFFICIENT_COVERAGE,
    GATE_SUFFICIENT,
    compute_coverage_gate,
)
from app.domain.research_depth import configure_budget_pools
from app.domain.schema import Budget
from app.graph.builder import after_critic
from app.graph.nodes.critic import critic_node
from app.graph.nodes.report import _ensure_budget_gap_notice, _synthesis_status


SLOTS = [
    {"id": "mechanism", "label": "Mechanism", "critical": True, "status": "open", "patterns": [r"batch"]},
    {"id": "latency", "label": "Latency", "critical": True, "status": "open", "patterns": [r"latency"]},
]


def _thin_evidence():
    return [
        {
            "id": "e1",
            "title": "vLLM batching",
            "url": "https://arxiv.org/abs/2301.00001",
            "snippet": "continuous batching improves throughput",
            "quote": "continuous batching improves throughput",
            "tier": "peer_reviewed",
            "credibility": 0.8,
        }
    ]


def test_compute_coverage_gate_budget_vs_coverage():
    budget = configure_budget_pools(Budget(max_iterations=2, iterations=2), "deep")
    gate = compute_coverage_gate(
        coverage_ok=False,
        critic_status="insufficient",
        budget=budget,
        can_loop=False,
    )
    assert gate["gate_reason"] == GATE_INSUFFICIENT_BUDGET
    assert gate["budget_exhausted"] is True

    budget2 = configure_budget_pools(Budget(max_iterations=3, iterations=1), "deep")
    gate2 = compute_coverage_gate(
        coverage_ok=False,
        critic_status="insufficient",
        budget=budget2,
        can_loop=True,
    )
    assert gate2["gate_reason"] == GATE_INSUFFICIENT_COVERAGE
    assert gate2["can_loop"] is True


def test_critic_node_sets_gate_reason_on_budget_exhausted():
    budget = configure_budget_pools(Budget(max_iterations=2, iterations=2, used_retrieval_calls=28), "deep")
    state = {
        "query": "How does vLLM batch requests for LoRA adapters?",
        "retrieved": _thin_evidence(),
        "brief": {"must_answer": SLOTS, "depth": "deep"},
        "budget": budget.model_dump(),
    }
    out = critic_node(state)
    assert out["critic"]["status"] == "insufficient"
    assert out["critic"]["gate_reason"] == GATE_INSUFFICIENT_BUDGET
    assert out["critic"]["coverage_gate"]["budget_exhausted"] is True
    assert not out["followups"]


def test_critic_node_loops_when_budget_remains():
    budget = configure_budget_pools(Budget(max_iterations=3, iterations=1), "deep")
    state = {
        "query": "How does vLLM batch requests for LoRA adapters?",
        "retrieved": _thin_evidence(),
        "brief": {"must_answer": SLOTS, "depth": "deep"},
        "budget": budget.model_dump(),
    }
    out = critic_node(state)
    assert out["critic"]["status"] == "insufficient"
    assert out["critic"]["gate_reason"] == GATE_INSUFFICIENT_COVERAGE
    assert out["followups"]


def test_after_critic_routes_budget_exhausted_to_hitl():
    budget = configure_budget_pools(Budget(max_iterations=2, iterations=2), "deep")
    state = {
        "budget": budget.model_dump(),
        "critic": {
            "status": "insufficient",
            "gate_reason": GATE_INSUFFICIENT_BUDGET,
            "followup_queries": [],
        },
    }
    assert after_critic(state) == "hitl"


from unittest.mock import patch


@patch("app.graph.nodes.report.llm")
def test_synthesis_status_terminal_on_insufficient_budget_gate(mock_llm):
    mock_llm.available = True
    budget = configure_budget_pools(Budget(max_iterations=2, iterations=2), "deep")
    critic = {
        "status": "insufficient",
        "gate_reason": GATE_INSUFFICIENT_BUDGET,
        "coverage_gate": {"gate_reason": GATE_INSUFFICIENT_BUDGET},
    }
    assert _synthesis_status({"budget": budget.model_dump()}, critic) == "terminal_fallback"


def test_ensure_budget_gap_notice_injects_banner():
    body = "# Title\n\n## Executive summary\n\nPartial answer.\n"
    out = _ensure_budget_gap_notice(body, "terminal_fallback")
    assert "could not be verified within the research budget" in out.lower()


def test_synthesis_status_sufficient_gate_not_terminal():
    budget = configure_budget_pools(Budget(max_iterations=3, iterations=1), "deep")
    critic = {
        "status": "sufficient",
        "gate_reason": GATE_SUFFICIENT,
        "coverage_gate": {"gate_reason": GATE_SUFFICIENT},
    }
    # May be heuristic_no_key if no LLM in test env
    status = _synthesis_status({"budget": budget.model_dump()}, critic)
    assert status != "terminal_fallback" or status == "heuristic_no_key"
