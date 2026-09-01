"""Coverage gate metadata: distinguish sufficient vs budget-blocked vs open gaps."""

from __future__ import annotations

from typing import Any

GATE_SUFFICIENT = "sufficient"
GATE_INSUFFICIENT_COVERAGE = "insufficient_coverage"
GATE_INSUFFICIENT_BUDGET = "insufficient_budget"
GATE_CONTRADICTED = "contradicted"

_GATE_MESSAGES = {
    GATE_SUFFICIENT: "Must-answer coverage met — safe to approve when the evidence looks right.",
    GATE_INSUFFICIENT_COVERAGE: "Open must-answer gaps remain — another retrieval loop may close them.",
    GATE_INSUFFICIENT_BUDGET: (
        "Research budget exhausted before all must-answer slots were covered. "
        "Approve only if you accept partial evidence."
    ),
    GATE_CONTRADICTED: "Material contradictions remain — read Contradictions & debates before approving.",
}


def budget_blocks_loop(budget: Any) -> bool:
    remaining_iterations = int(getattr(budget, "remaining_iterations", 0) or 0)
    remaining_calls = int(getattr(budget, "remaining_calls", 0) or 0)
    if hasattr(budget, "exhausted"):
        if budget.exhausted:
            return True
    return remaining_iterations <= 0 or remaining_calls <= 0


def gate_message(gate_reason: str) -> str:
    return _GATE_MESSAGES.get(gate_reason, _GATE_MESSAGES[GATE_INSUFFICIENT_COVERAGE])


def compute_coverage_gate(
    *,
    coverage_ok: bool,
    critic_status: str,
    budget: Any,
    can_loop: bool,
) -> dict[str, Any]:
    """Return structured gate metadata for critic, HITL, memo_gate, and report metrics."""
    exhausted = budget_blocks_loop(budget)
    status = (critic_status or "").strip().lower()
    loop_open = bool(can_loop) and not exhausted

    if status == "contradicted":
        gate_reason = GATE_CONTRADICTED
    elif coverage_ok and status == "sufficient":
        gate_reason = GATE_SUFFICIENT
    elif not coverage_ok and not loop_open:
        gate_reason = GATE_INSUFFICIENT_BUDGET
    elif not coverage_ok:
        gate_reason = GATE_INSUFFICIENT_COVERAGE
    else:
        gate_reason = GATE_SUFFICIENT

    return {
        "gate_reason": gate_reason,
        "coverage_ok": bool(coverage_ok),
        "can_loop": loop_open,
        "budget_exhausted": exhausted,
        "message": gate_message(gate_reason),
    }


def gate_from_critic(critic: dict | None) -> dict[str, Any]:
    if not critic:
        return {}
    nested = critic.get("coverage_gate")
    if isinstance(nested, dict) and nested.get("gate_reason"):
        return nested
    reason = critic.get("gate_reason")
    if reason:
        return {
            "gate_reason": reason,
            "coverage_ok": critic.get("coverage_ok"),
            "budget_exhausted": critic.get("budget_exhausted"),
            "message": critic.get("gate_message") or gate_message(str(reason)),
        }
    return {}
