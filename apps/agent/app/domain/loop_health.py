"""Diminishing-returns circuit breaker for research loops."""

from __future__ import annotations

from typing import Any

from app.domain.source_dedup import unique_source_count

GROWTH_FLOOR = 0.12
STAGNANT_LIMIT = 2


def assess_loop_health(
    evidence: list[dict],
    *,
    prev_unique_sources: int = 0,
    stagnant_loops: int = 0,
) -> dict[str, Any]:
    curr = unique_source_count(evidence)
    delta = max(0, curr - int(prev_unique_sources or 0))
    base = max(1, int(prev_unique_sources or 0))
    growth_pct = round(delta / base, 3)
    stagnant = int(stagnant_loops or 0)
    if growth_pct < GROWTH_FLOOR:
        stagnant += 1
    else:
        stagnant = 0
    return {
        "unique_sources": curr,
        "new_sources": delta,
        "growth_pct": growth_pct,
        "stagnant_loops": stagnant,
        "circuit_break": stagnant >= STAGNANT_LIMIT,
        "reason": (
            "New evidence growth below threshold for "
            f"{stagnant} consecutive loop(s) — stopping expensive retrieval."
            if stagnant >= STAGNANT_LIMIT
            else ""
        ),
    }
