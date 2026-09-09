from app.domain.gap_enrich import coverage_slots_from_state, urls_for_gap_slots
from app.domain.schema import Budget
from app.graph.nodes import enrich as enrich_mod
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


def _base_state(url: str) -> dict:
    return {
        "brief": {"depth": "standard"},
        "evidence": [{"url": url, "title": "Paper"}],
        "budget": Budget(max_iterations=2, max_tool_calls=20, max_enrich_calls=10).model_dump(mode="json"),
        "critic": {},
    }


def test_enrich_node_fetches_arxiv_html_not_abs(monkeypatch):
    """Regression: fetching an arXiv /abs/ID page returns almost nothing (a
    short abstract blurb + nav chrome) while /html/ID is the full paper. A
    real run had full_text on 9/20 sources — every success was already an
    /html/ link, every /abs/ link came back empty."""
    calls: list[str] = []

    def fake_fetch(url: str, title: str = "") -> dict:
        calls.append(url)
        return {"id": "ev_x", "title": title or url, "url": url, "snippet": "x" * 100, "full_text": "x" * 100}

    monkeypatch.setattr(enrich_mod, "evidence_from_url", fake_fetch)
    enrich_mod.enrich_node(_base_state("http://arxiv.org/abs/2312.11970"))
    assert calls == ["https://arxiv.org/html/2312.11970"]


def test_enrich_node_falls_back_to_abs_when_html_unavailable(monkeypatch):
    def fake_fetch(url: str, title: str = "") -> dict | None:
        if "/html/" in url:
            return None
        return {"id": "ev_x", "title": title or url, "url": url, "snippet": "x" * 100, "full_text": "x" * 100}

    monkeypatch.setattr(enrich_mod, "evidence_from_url", fake_fetch)
    out = enrich_mod.enrich_node(_base_state("http://arxiv.org/abs/2312.11970"))
    extra = out["evidence"]
    assert extra and extra[0]["url"] == "http://arxiv.org/abs/2312.11970"
