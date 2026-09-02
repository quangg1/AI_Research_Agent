"""Research depth policy and split retrieval/enrich budget pools."""

from __future__ import annotations

from app.domain.retrieval_limits import DEEP_RESERVE_CALLS, ENRICH_POOL, RETRIEVAL_POOL
from app.domain.schema import Budget

FORCED_DEPTH = "deep"


def effective_depth(brief: dict | None = None) -> str:
    """Kiln always runs deep research — UI may show depth but pipeline forces deep."""
    return FORCED_DEPTH


def apply_forced_depth(brief: dict | None) -> dict:
    out = dict(brief or {})
    out["depth"] = FORCED_DEPTH
    return out


def configure_budget_pools(budget: Budget, depth: str | None = None) -> Budget:
    """Assign independent search/scholar vs enrich call pools."""
    from app.config import settings

    d = (depth or FORCED_DEPTH).lower()
    if d not in RETRIEVAL_POOL:
        d = FORCED_DEPTH
    if settings.showcase_mode:
        budget.max_retrieval_calls = settings.showcase_retrieval_pool
        budget.max_enrich_calls = settings.showcase_enrich_pool
    else:
        budget.max_retrieval_calls = RETRIEVAL_POOL[d]
        budget.max_enrich_calls = ENRICH_POOL[d]
    budget.max_tool_calls = budget.max_retrieval_calls + budget.max_enrich_calls
    budget.sync_totals()
    return budget


def showcase_reserve_calls(depth: str, iteration: int) -> int:
    from app.config import settings

    if settings.showcase_mode and depth == "deep" and iteration == 1:
        return settings.showcase_reserve_calls
    return {"quick": 2, "standard": 6, "deep": DEEP_RESERVE_CALLS}.get(depth, 6)
