from app.domain.gap_enrich import coverage_slots_from_state, urls_for_gap_slots
from app.graph.nodes.enrich import gap_slots_with_gaps


def test_coverage_slots_from_critic():
    state = {
        "critic": {
            "coverage": {
                "slots": [
                    {"id": "mechanism", "status": "weak", "critical": True, "evidence_ids": ["e1"]}
                ]
            }
        }
    }
    slots = coverage_slots_from_state(state)
    assert slots[0]["id"] == "mechanism"


def test_urls_for_gap_slots_includes_fetchable_blog():
    slots = [
        {
            "id": "mechanism",
            "critical": True,
            "status": "weak",
            "evidence_ids": ["e2"],
            "patterns": [r"kernel"],
            "topic_terms": ["lora"],
        }
    ]
    evidence = [
        {"id": "e1", "url": "https://arxiv.org/abs/2310.1", "snippet": "short", "title": "paper"},
        {"id": "e2", "url": "https://medium.com/@user/lora-kernels", "snippet": "thin", "title": "blog"},
    ]
    gap_urls = urls_for_gap_slots(evidence, slots, limit=3)
    assert "medium.com" in gap_urls[0]


def test_iter2_enrich_orders_gap_urls_first():
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
        {"id": "e1", "url": "https://arxiv.org/abs/2310.1", "snippet": "kernel", "title": "paper"},
        {"id": "e2", "url": "https://medium.com/x", "snippet": "blog", "title": "blog"},
    ]
    gap_urls = urls_for_gap_slots(evidence, slots, limit=3)
    assert gap_urls[0].startswith("https://arxiv.org")
    assert gap_slots_with_gaps(slots)
