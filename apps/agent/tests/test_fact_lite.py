from app.domain.fact_lite import verify_memo_citations
from app.report.deep_write import prioritize_dossier_for_writer


def test_prioritize_dossier_open_before_covered():
    dossier = [
        {"id": "a", "status": "covered", "label": "A"},
        {"id": "b", "status": "open", "label": "B"},
        {"id": "c", "status": "weak", "label": "C"},
    ]
    ordered = prioritize_dossier_for_writer(dossier)
    assert [d["id"] for d in ordered] == ["b", "c", "a"]


def test_fact_lite_verifies_quote_in_evidence():
    md = "Throughput improved under batching [1]."
    citations = [{"n": 1, "url": "https://arxiv.org/abs/2401.1", "title": "Paper", "quote": "batching improves throughput"}]
    evidence = [
        {
            "url": "https://arxiv.org/abs/2401.1",
            "snippet": "batching improves throughput on the benchmark",
        }
    ]
    out = verify_memo_citations(md, citations, evidence)
    assert out["checked"] == 1
    assert out["verified"] == 1
    assert out["accuracy"] == 1.0


def test_fact_lite_flags_missing_quote():
    md = "Claim without backing [2]."
    citations = [{"n": 2, "url": "https://example.com/x", "title": "X", "quote": ""}]
    evidence = [{"url": "https://example.com/y", "snippet": "other"}]
    out = verify_memo_citations(md, citations, evidence)
    assert out["verified"] == 0
    assert out["failures"]
