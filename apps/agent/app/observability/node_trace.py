"""Lightweight per-node timing helpers for traces[]."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.domain.research_depth import effective_depth


@contextmanager
def trace_span(node: str, **start_meta: Any) -> Iterator[dict[str, Any]]:
    """Accumulate duration_ms and optional token deltas on a trace dict."""
    meta: dict[str, Any] = {"node": node, **start_meta}
    started = time.perf_counter()
    try:
        yield meta
    finally:
        meta["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)


def fanout_parallelism(state: dict[str, Any], *, ceiling: int = 3) -> int:
    depth = effective_depth(state.get("brief") or {})
    by_depth = {"quick": 1, "standard": 2, "deep": min(5, ceiling)}
    return max(1, min(ceiling, by_depth.get(depth, 2)))
