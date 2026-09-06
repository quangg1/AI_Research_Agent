"""Per-slot enrich/retrieval budget hints and adaptive reclaim (T2-6)."""

from __future__ import annotations

from typing import Any

_SLOT_WEIGHTS: dict[str, float] = {
    "quantitative": 3.0,
    "scalability": 2.0,
    "comparison": 2.0,
    "mechanism": 1.5,
    "implementation": 1.5,
    "direct_answer": 1.0,
    "constraints": 1.0,
    "worked_example": 1.0,
    "procedure": 1.0,
    "recency": 1.0,
    "caveats": 0.75,
}


def _open_slots(slots: list[dict]) -> list[dict]:
    return [s for s in (slots or []) if str(s.get("status") or "").lower() in {"open", "weak"}]


def slot_weight(slot: dict) -> float:
    sid = str(slot.get("id") or "").lower()
    if sid in _SLOT_WEIGHTS:
        return _SLOT_WEIGHTS[sid]
    label = str(slot.get("label") or "").lower()
    if "quant" in sid or "benchmark" in label or "latency" in label:
        return 3.0
    if slot.get("critical"):
        return 1.5
    return 1.0


def slot_enrich_quota(
    open_slots: list[dict],
    *,
    total_cap: int,
    min_per_slot: int = 1,
) -> dict[str, int]:
    """Weighted enrich fetch budget across open coverage slots."""
    gaps = _open_slots(open_slots)
    if not gaps or total_cap <= 0:
        return {}
    weights = [max(0.1, slot_weight(s)) for s in gaps]
    total_w = sum(weights) or 1.0
    out: dict[str, int] = {}
    remaining = total_cap
    for slot, weight in zip(gaps, weights):
        sid = str(slot.get("id") or slot.get("label") or "")
        if not sid or remaining <= 0:
            continue
        share = max(min_per_slot, int(round(total_cap * (weight / total_w))))
        take = min(share, remaining)
        out[sid] = take
        remaining -= take
    return out


def reclaim_enrich_cap(
    fetch_cap: int,
    slot_quotas: dict[str, int],
    slot_used: dict[str, int],
    slots: list[dict],
) -> int:
    """Return effective enrich cap after reclaiming quota from covered slots."""
    bonus = 0
    for sid, quota in (slot_quotas or {}).items():
        slot = next((s for s in (slots or []) if str(s.get("id") or s.get("label") or "") == sid), None)
        if slot and str(slot.get("status") or "").lower() == "covered":
            bonus += max(0, int(quota) - int((slot_used or {}).get(sid) or 0))
    return int(fetch_cap) + bonus


def adaptive_retrieval_reserve(
    slots: list[dict],
    *,
    depth: str,
    iteration: int,
    base_reserve: int,
) -> int:
    """Shrink iter-1 retrieval reserve when non-critical slots are already covered."""
    if iteration != 1 or base_reserve <= 0:
        return 0 if iteration > 1 else base_reserve
    if not slots:
        return base_reserve
    open_critical = [
        s for s in (slots or []) if s.get("critical") and str(s.get("status") or "").lower() in {"open", "weak"}
    ]
    covered_noncritical = [
        s for s in (slots or []) if not s.get("critical") and str(s.get("status") or "").lower() == "covered"
    ]
    reclaimed = min(base_reserve - 2, len(covered_noncritical) * 2)
    if not open_critical and covered_noncritical:
        reclaimed = max(reclaimed, base_reserve // 2)
    if len(open_critical) <= 1:
        reclaimed = max(reclaimed, 3)
    return max(2, base_reserve - reclaimed)


def _simulate_slot_enrich(
    slots: list[dict[str, Any]],
    *,
    total_cap: int,
    strategy: str,
) -> dict[str, int]:
    """Return per-slot enrich calls used under a strategy ('global' | 'quota' | 'reclaim')."""
    all_needs = {str(s["id"]): int(s.get("enrich_need") or 0) for s in slots if s.get("id")}
    open_gaps = _open_slots(slots)
    open_needs = {str(s["id"]): int(s.get("enrich_need") or 0) for s in open_gaps}
    used: dict[str, int] = {sid: 0 for sid in all_needs}
    pool = total_cap

    if strategy == "global":
        # Legacy behavior: spend on every slot with declared need, including already-covered slots.
        order = sorted(all_needs.keys(), key=lambda sid: -all_needs[sid])
        for sid in order:
            take = min(all_needs[sid], pool)
            used[sid] = take
            pool -= take
        return used

    quotas = slot_enrich_quota(open_gaps, total_cap=total_cap)
    slot_used: dict[str, int] = {sid: 0 for sid in open_needs}
    pool_left = total_cap

    for sid in sorted(open_needs, key=lambda s: -open_needs.get(s, 0)):
        cap = quotas.get(sid, 0)
        if strategy == "reclaim":
            slot = next((s for s in slots if str(s.get("id")) == sid), {})
            if str(slot.get("status") or "").lower() == "covered":
                cap = 0
        take = min(open_needs.get(sid, 0), cap, pool_left)
        slot_used[sid] = take
        pool_left -= take

    if strategy == "reclaim":
        covered_bonus = reclaim_enrich_cap(0, quotas, slot_used, slots)
        pool_left += covered_bonus
        for sid in sorted(open_needs, key=lambda s: -open_needs[s]):
            if pool_left <= 0:
                break
            if slot_used.get(sid, 0) >= open_needs[sid]:
                continue
            extra = min(open_needs[sid] - slot_used[sid], pool_left)
            slot_used[sid] += extra
            pool_left -= extra

    for sid, val in slot_used.items():
        used[sid] = val
    return used


def _misallocated_enrich(used: dict[str, int], slots: list[dict[str, Any]]) -> int:
    """Calls spent on covered slots + unused cap (proxy for waste)."""
    covered_ids = {
        str(s.get("id"))
        for s in slots
        if str(s.get("status") or "").lower() == "covered" and s.get("id")
    }
    on_covered = sum(used.get(sid, 0) for sid in covered_ids)
    on_open = sum(v for sid, v in used.items() if sid not in covered_ids)
    open_need = sum(int(s.get("enrich_need") or 0) for s in _open_slots(slots))
    starved = max(0, open_need - on_open)
    return on_covered + starved


def simulate_enrich_waste(slots: list[dict[str, Any]], *, total_cap: int) -> dict[str, Any]:
    """Compare misallocated enrich budget: global pool vs weighted quota vs quota+reclaim."""
    global_used = _simulate_slot_enrich(slots, total_cap=total_cap, strategy="global")
    quota_used = _simulate_slot_enrich(slots, total_cap=total_cap, strategy="quota")
    reclaim_used = _simulate_slot_enrich(slots, total_cap=total_cap, strategy="reclaim")

    global_waste = _misallocated_enrich(global_used, slots)
    quota_waste = _misallocated_enrich(quota_used, slots)
    reclaim_waste = _misallocated_enrich(reclaim_used, slots)
    baseline = global_waste or 1
    quota_improvement_pct = int(round(100 * (1 - quota_waste / baseline)))
    reclaim_improvement_pct = int(round(100 * (1 - reclaim_waste / baseline)))
    return {
        "total_cap": total_cap,
        "global_waste": global_waste,
        "quota_waste": quota_waste,
        "reclaim_waste": reclaim_waste,
        "quota_improvement_pct": quota_improvement_pct,
        "reclaim_improvement_pct": reclaim_improvement_pct,
        "needs_full_reclaim": quota_improvement_pct < 70,
    }
