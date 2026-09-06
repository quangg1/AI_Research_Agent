from app.domain.decision_export import build_decision_payload
from app.domain.evidence_cache import lookup_verified_rows, reset_cache, store_verified_rows
from app.domain.slot_budget import slot_enrich_quota


def test_build_decision_payload_shapes_claims():
    payload = build_decision_payload(
        query="LoRA latency",
        title="Memo",
        executive_summary="Summary",
        decision_rule="Use batching",
        at_a_glance="Batch",
        claims=[{"id": "c1", "text": "Batching helps", "confidence": 0.8, "support_ids": ["e1"]}],
        citations=[{"n": 1, "url": "https://a.org", "title": "Paper"}],
        open_questions=["What about tail latency?"],
        limitations=["Thin GPU evidence"],
        critic={"confidence_floor": 0.7, "depth_score": {"score": 72}},
        coverage={"gaps": ["scalability"]},
        metrics={"synthesis_status": "gemini_success"},
    )
    assert payload["query"] == "LoRA latency"
    assert payload["claims"][0]["id"] == "c1"
    assert payload["confidence"]["depth_score"] == 72


def test_slot_enrich_quota_splits_open_slots():
    quotas = slot_enrich_quota(
        [{"id": "a", "status": "open"}, {"id": "b", "status": "weak"}, {"id": "c", "status": "covered"}],
        total_cap=6,
    )
    assert quotas.get("a", 0) >= 1
    assert quotas.get("b", 0) >= 1
    assert "c" not in quotas


def test_verified_evidence_cache_roundtrip():
    reset_cache()
    rows = [{"url": "https://arxiv.org/abs/1", "metric": "92%", "n": 1}]
    store_verified_rows("org_a", "fp1", rows)
    hit = lookup_verified_rows("org_a", "fp1")
    assert hit and hit[0]["metric"] == "92%"
    assert not lookup_verified_rows("org_b", "fp1")
