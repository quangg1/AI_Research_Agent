from app.domain.citations import arxiv_pdf_fallback
from app.domain.schema import AgentName
from app.graph.nodes.enrich import (
    _is_scholar_paper_url,
    _marginal_fetch_value,
    _reserve_scholar_urls,
    _staged_batches,
)
from app.tools.fetch import _fetch_page_text, evidence_from_url


SYNTH_Q = (
    "Analyze synthetic data generation methodologies and evaluation frameworks for "
    "high-quality data scarcity in specialized AI domains."
)


def test_arxiv_pdf_fallback_maps_abs_and_html():
    assert arxiv_pdf_fallback("https://arxiv.org/abs/2401.12345") == "https://arxiv.org/pdf/2401.12345.pdf"
    assert arxiv_pdf_fallback("https://arxiv.org/html/2401.12345") == "https://arxiv.org/pdf/2401.12345.pdf"
    assert arxiv_pdf_fallback("https://example.com/paper") == ""


def test_fetch_page_text_prefers_pdf_for_arxiv(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url: str, timeout: float = 12.0) -> str:
        calls.append(url)
        if "pdf" in url:
            return "x" * 120 + " accuracy 91.2% benchmark ablation results across datasets"
        return "Short abstract only."

    monkeypatch.setattr("app.tools.fetch.fetch_url", fake_fetch)
    text = _fetch_page_text("https://arxiv.org/abs/2401.12345")
    assert calls[0].endswith(".pdf")
    assert "accuracy" in text
    assert len(text) >= 80


def test_evidence_from_url_uses_pdf_body(monkeypatch):
    monkeypatch.setattr(
        "app.tools.fetch._fetch_page_text",
        lambda url: "x" * 120 + " measured F1 0.82 latency throughput benchmark",
    )
    row = evidence_from_url("https://arxiv.org/abs/2401.12345", title="Synthetic Data Survey")
    assert row is not None
    assert row["url"] == "https://arxiv.org/abs/2401.12345"
    assert len(row["full_text"]) >= 80


def test_reserve_scholar_urls_pins_papers_first():
    evidence = [
        {"url": "https://nvidia.com/blog", "source_agent": AgentName.SEARCH.value},
        {
            "url": "https://arxiv.org/abs/2401.1",
            "source_agent": AgentName.SCHOLAR.value,
            "title": "Paper A",
        },
        {"url": "https://github.com/org/repo", "source_agent": AgentName.SEARCH.value},
        {
            "url": "https://aclanthology.org/2024.acl-long.1",
            "source_agent": AgentName.SCHOLAR.value,
            "title": "Paper B",
        },
    ]
    urls = [e["url"] for e in evidence]
    ordered = _reserve_scholar_urls(evidence, urls, reserve=6)
    assert ordered[0] == "https://arxiv.org/abs/2401.1"
    assert ordered[1] == "https://aclanthology.org/2024.acl-long.1"
    assert ordered[2] == "https://nvidia.com/blog"


def test_is_scholar_paper_url_requires_scholar_agent():
    ev = {"source_agent": AgentName.SCHOLAR.value}
    assert _is_scholar_paper_url(ev, "https://arxiv.org/abs/2401.1")
    assert not _is_scholar_paper_url(
        {"source_agent": AgentName.SEARCH.value},
        "https://arxiv.org/abs/2401.1",
    )


def test_methodology_probe_does_not_stop_early_on_low_marginal():
    urls = [f"https://example.com/{i}" for i in range(8)]
    probe_n = 3
    batches = list(_staged_batches(urls, probe_n, fetch_cap=6))
    assert batches[0][0] == "probe"
    assert len(batches[0][1]) == 3
    assert batches[1][0] == "expand"
    assert not _marginal_fetch_value([], [{"full_text": "tiny"}], 0)


def test_methodology_query_constant_for_routing_tests():
    from app.domain.research_intent import is_methodology_eval_query

    assert is_methodology_eval_query(SYNTH_Q)


def test_enrich_node_skips_early_stop_for_methodology(monkeypatch):
    from app.domain.schema import Budget
    from app.graph.nodes.enrich import enrich_node

    evidence = [
        {
            "url": f"https://example.com/page-{i}",
            "title": f"Distinct research topic alpha beta gamma delta {i}",
            "snippet": (
                f"Section {i}: methodology covers dataset curation, evaluation metrics, "
                f"and benchmark protocol variant {i * 17} for specialized domains."
            ),
        }
        for i in range(10)
    ]
    state = {
        "query": SYNTH_Q,
        "brief": {"depth": "deep"},
        "evidence": evidence,
        "budget": Budget(max_iterations=2, max_tool_calls=52, max_enrich_calls=30).model_dump(mode="json"),
        "coverage_slots": [],
    }

    def thin_fetch(url: str, title: str = "", body: str = "") -> dict | None:
        return {
            "id": "ev_x",
            "title": title or url,
            "url": url,
            "snippet": "short",
            "full_text": "short",
            "fetch_status": "ok",
        }

    monkeypatch.setattr("app.graph.nodes.enrich.evidence_from_url", thin_fetch)
    monkeypatch.setattr(
        "app.graph.nodes.enrich.select_urls_for_enrich",
        lambda evidence, urls, **kwargs: (urls, {"kept": len(urls), "dropped_duplicate": 0, "dropped_access": 0}),
    )
    out = enrich_node(state)
    trace = out["traces"][0]
    assert trace.get("methodology_mode") is True
    assert "stopped_early" not in trace
    assert trace["fetched"] > 4


def test_enrich_node_stops_early_for_non_methodology(monkeypatch):
    from app.domain.schema import Budget
    from app.graph.nodes.enrich import enrich_node

    evidence = [
        {"url": f"https://example.com/page-{i}", "title": f"Topic {i}", "snippet": f"Unique content block {i} xyz"}
        for i in range(10)
    ]
    state = {
        "query": "How does Punica batch LoRA adapters?",
        "brief": {"depth": "deep"},
        "evidence": evidence,
        "budget": Budget(max_iterations=2, max_tool_calls=52, max_enrich_calls=30).model_dump(mode="json"),
        "coverage_slots": [],
    }

    def thin_fetch(url: str, title: str = "", body: str = "") -> dict | None:
        return {
            "id": "ev_x",
            "title": title or url,
            "url": url,
            "snippet": "short",
            "full_text": "short",
            "fetch_status": "ok",
        }

    monkeypatch.setattr("app.graph.nodes.enrich.evidence_from_url", thin_fetch)
    monkeypatch.setattr(
        "app.graph.nodes.enrich.select_urls_for_enrich",
        lambda evidence, urls, **kwargs: (urls, {"kept": len(urls), "dropped_duplicate": 0, "dropped_access": 0}),
    )
    out = enrich_node(state)
    trace = out["traces"][0]
    assert trace.get("methodology_mode") is False
    assert trace.get("stopped_early") == "low_marginal_value"
    assert trace["fetched"] <= 4
