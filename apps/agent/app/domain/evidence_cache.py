"""Cross-run verified evidence snippets — org-scoped with TTL (T3-8)."""

from __future__ import annotations

import threading
import time
from typing import Any

from app.config import settings

_CACHE_LOCK = threading.RLock()
_VERIFIED: dict[str, list[dict[str, Any]]] = {}


def _cache_key(org_id: str | None, fingerprint: str) -> str:
    if settings.require_org_id and not (org_id or "").strip():
        raise ValueError("org_id required for evidence cache when REQUIRE_ORG_ID=true")
    scope = (org_id or "").strip() or "__anonymous__"
    return f"{scope}::{fingerprint}"


def _ttl_seconds() -> int:
    days = max(1, int(getattr(settings, "evidence_cache_ttl_days", 30) or 30))
    return days * 86400


def _fresh_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = time.time() - _ttl_seconds()
    fresh: list[dict[str, Any]] = []
    for row in rows or []:
        cached_at = float(row.get("_cached_at") or 0)
        if cached_at >= cutoff:
            fresh.append(row)
    return fresh


def store_verified_rows(
    org_id: str | None,
    fingerprint: str,
    rows: list[dict[str, Any]],
    *,
    limit: int = 24,
) -> None:
    """Persist high-confidence quantitative rows for reuse on similar queries."""
    if not getattr(settings, "evidence_cache_enabled", True):
        return
    if not fingerprint or not rows:
        return
    key = _cache_key(org_id, fingerprint)
    now = time.time()
    with _CACHE_LOCK:
        bucket = _fresh_rows(list(_VERIFIED.get(key) or []))
        seen = {r.get("url") for r in bucket}
        for row in rows:
            url = row.get("url")
            if not url or url in seen:
                continue
            stored = {**row, "_cached_at": now, "_org_id": org_id or None}
            bucket.append(stored)
            seen.add(url)
        _VERIFIED[key] = bucket[-limit:]


def lookup_verified_rows(org_id: str | None, fingerprint: str) -> list[dict[str, Any]]:
    if not getattr(settings, "evidence_cache_enabled", True):
        return []
    if not fingerprint:
        return []
    key = _cache_key(org_id, fingerprint)
    with _CACHE_LOCK:
        rows = _fresh_rows(list(_VERIFIED.get(key) or []))
        _VERIFIED[key] = rows
        return [dict(r) for r in rows]


def reset_cache() -> None:
    with _CACHE_LOCK:
        _VERIFIED.clear()
