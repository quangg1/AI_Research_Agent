"""Cross-source reconciliation for quantitative evidence before verification."""

from __future__ import annotations

import re
from typing import Any

from app.domain.adversarial import extract_quantitative_rows

_PCT_VAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_NUM_VAL_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _band_rank(row: dict[str, Any]) -> int:
    order = {"S": 0, "A": 1, "B": 2, "C": 3}
    return order.get(str(row.get("band") or "C").upper(), 3)


def _best_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        rows,
        key=lambda r: (_band_rank(r), str(r.get("year") or "")),
        reverse=False,
    )[0]


def _numeric_value(token: str) -> float | None:
    m = _PCT_VAL_RE.search(token or "") or _NUM_VAL_RE.search(token or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _group_key(row: dict[str, Any]) -> str:
    bench = str(row.get("benchmark_name") or row.get("condition") or "general").lower()
    bench = re.sub(r"\s+", " ", bench)[:80]
    metric = str(row.get("metric_name") or row.get("metric") or row.get("value") or "").lower()
    metric = re.sub(r"\d+(?:\.\d+)?%?", "", metric).strip() or "metric"
    return f"{bench}|{metric}"


def reconcile_quantitative_rows(
    evidence: list[dict],
    citations: list[dict] | None = None,
    *,
    rel_tolerance: float = 0.12,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop lower-trust duplicates; surface unresolved cross-source conflicts."""
    rows = extract_quantitative_rows(evidence, citations)
    if not rows:
        return [], []

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_group_key(row), []).append(row)

    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    for key, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        vals: list[tuple[float, dict[str, Any]]] = []
        for row in group:
            val = _numeric_value(str(row.get("value") or row.get("metric") or ""))
            if val is not None:
                vals.append((val, row))
        if len(vals) < 2:
            kept.append(_best_row(group))
            continue
        vals.sort(key=lambda item: item[0])
        low, high = vals[0][0], vals[-1][0]
        spread = abs(high - low) / max(abs(low), 1e-6)
        if spread <= rel_tolerance:
            kept.append(_best_row(group))
            continue
        winner = _best_row(group)
        kept.append(winner)
        sources = sorted({str(r.get("n")) for r in group if r.get("n") not in (None, "?")})
        notes.append(
            f"Cross-source conflict on {key}: "
            f"values span {low}–{high} across [{', '.join(sources)}]; kept highest-band measured row [{winner.get('n')}]."
        )
    return kept, notes
