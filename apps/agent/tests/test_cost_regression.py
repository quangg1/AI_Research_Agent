from app.domain.research_depth import effective_depth
from app.domain.retrieval_limits import ENRICH_FETCH_CAP
from app.domain.schema import Budget
from app.domain.slot_budget import (
    adaptive_retrieval_reserve,
    reclaim_enrich_cap,
    simulate_enrich_waste,
    slot_enrich_quota,
)
from app.graph.nodes.enrich import _enrich_cap


def _skewed_slots():
    return [
        {"id": "direct_answer", "status": "covered", "critical": True, "enrich_need": 10},
        {"id": "quantitative", "status": "open", "critical": True, "enrich_need": 4},
        {"id": "scalability", "status": "weak", "critical": False, "enrich_need": 1},
    ]


def test_skewed_workload_quota_improves_over_global():
    stats = simulate_enrich_waste(_skewed_slots(), total_cap=6)
    assert stats["quota_improvement_pct"] >= 70
    assert stats["needs_full_reclaim"] is False
    assert stats["global_waste"] > stats["quota_waste"]


def test_reclaim_beats_quota_when_slot_covered_early():
    slots = _skewed_slots()
    quotas = {"direct_answer": 2, "quantitative": 4}
    effective = reclaim_enrich_cap(6, quotas, {"direct_answer": 0}, slots)
    assert effective > 6


def test_adaptive_reserve_reclaims_when_noncritical_covered():
    slots = [
        {"id": "direct_answer", "status": "covered", "critical": True},
        {"id": "mechanism", "status": "covered", "critical": False},
        {"id": "quantitative", "status": "open", "critical": True},
    ]
    reserve = adaptive_retrieval_reserve(slots, depth="deep", iteration=1, base_reserve=10)
    assert reserve < 10
    assert reserve >= 2


def test_adaptive_reserve_unchanged_without_slot_signal():
    assert adaptive_retrieval_reserve([], depth="deep", iteration=1, base_reserve=10) == 10


def test_enrich_cap_respects_depth_pool():
    depth = effective_depth({"depth": "deep"})
    first, later = ENRICH_FETCH_CAP["deep"]
    assert _enrich_cap(depth, 1) == first
    assert _enrich_cap(depth, 2) == later


def test_enrich_budget_ceiling_for_fixture_iteration():
    cap = _enrich_cap("deep", 1)
    budget = Budget(max_enrich_calls=cap, used_enrich_calls=0)
    assert budget.remaining_enrich_calls == cap
    budget.used_enrich_calls = cap
    assert budget.remaining_enrich_calls == 0
