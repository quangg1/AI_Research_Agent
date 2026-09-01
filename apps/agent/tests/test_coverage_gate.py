from app.domain.coverage_gate import (
    GATE_INSUFFICIENT_BUDGET,
    GATE_SUFFICIENT,
    compute_coverage_gate,
    gate_message,
)
from app.domain.schema import Budget


def test_gate_messages_are_distinct():
    assert "budget" in gate_message(GATE_INSUFFICIENT_BUDGET).lower()
    assert gate_message(GATE_SUFFICIENT) != gate_message(GATE_INSUFFICIENT_BUDGET)


def test_sufficient_when_coverage_ok():
    budget = Budget(max_iterations=3, iterations=1)
    gate = compute_coverage_gate(
        coverage_ok=True,
        critic_status="sufficient",
        budget=budget,
        can_loop=True,
    )
    assert gate["gate_reason"] == GATE_SUFFICIENT
    assert gate["coverage_ok"] is True
