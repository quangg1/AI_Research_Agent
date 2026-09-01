from app.domain.gap_enrich import (
    fetch_gap_evidence,
    gap_slots,
    merge_fetched_evidence,
    urls_for_gap_slots,
)
from app.domain.schema import Budget


def test_gap_slots_orders_critical_first():
    slots = [
        {"id": "a", "critical": False, "status": "open"},
        {"id": "b", "critical": True, "status": "weak"},
        {"id": "c", "critical": True, "status": "open"},
    ]
    ordered = gap_slots(slots)
    assert [s["id"] for s in ordered] == ["b", "c", "a"]


def test_urls_for_gap_slots_prefers_linked_thin_evidence():
    slots = [
        {
            "id": "mechanism",
            "critical": True,
            "status": "weak",
            "evidence_ids": ["e1"],
            "patterns": [r"kernel"],
            "topic_terms": ["lora"],
        }
    ]
    evidence = [
        {
            "id": "e1",
            "url": "https://arxiv.org/abs/2310.1",
            "snippet": "short",
            "title": "LoRA kernel paper",
        },
        {
            "id": "e2",
            "url": "https://medium.com/blog",
            "snippet": "long " * 400,
            "title": "blog",
        },
    ]
    urls = urls_for_gap_slots(evidence, slots, limit=2)
    assert urls[0] == "https://arxiv.org/abs/2310.1"


def test_merge_fetched_evidence_updates_existing_row():
    working = [{"id": "e1", "url": "https://arxiv.org/abs/1", "snippet": "short"}]
    fetched = [{"id": "e1", "url": "https://arxiv.org/abs/1", "full_text": "full paper text", "snippet": "short"}]
    merged = merge_fetched_evidence(working, fetched)
    assert len(merged) == 1
    assert "full paper text" in merged[0]["full_text"]


def test_fetch_gap_evidence_respects_budget(monkeypatch):
    slots = [
        {
            "id": "quant",
            "critical": True,
            "status": "open",
            "evidence_ids": [],
            "patterns": [r"latency"],
            "topic_terms": ["gpu"],
        }
    ]
    evidence = [
        {"id": "e1", "url": "https://arxiv.org/abs/2310.12345", "snippet": "gpu latency numbers", "title": "bench"}
    ]

    def fake_fetch(url: str, title: str = ""):
        return {"url": url, "title": title or "t", "full_text": "measured latency 42ms on gpu"}

    monkeypatch.setattr("app.domain.gap_enrich.evidence_from_url", fake_fetch)
    budget = Budget(max_enrich_calls=4, used_enrich_calls=2)
    rows, calls = fetch_gap_evidence(evidence, slots, budget, limit=2)
    assert calls == 1
    assert rows[0]["full_text"].startswith("measured latency")
    assert budget.used_enrich_calls == 3
    assert budget.used_retrieval_calls == 0
