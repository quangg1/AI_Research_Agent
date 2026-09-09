from app.graph.state import merge_unique_evidence
from app.report.memo_structure import apply_source_quality_from_ledger


def test_merge_unique_evidence_keeps_longer_full_text():
    left = [
        {
            "id": "a",
            "url": "https://arxiv.org/abs/123",
            "snippet": "short search excerpt without numbers",
            "title": "Paper A",
        }
    ]
    right = [
        {
            "id": "a",
            "url": "https://arxiv.org/abs/123",
            "full_text": "x" * 5000 + " accuracy 62% on MMLU n=1200",
            "snippet": "accuracy 62% on MMLU",
        }
    ]
    merged = merge_unique_evidence(left, right)
    assert len(merged) == 1
    assert len(merged[0].get("full_text") or "") >= 5000
    assert "62%" in (merged[0].get("full_text") or "")


def test_apply_source_quality_from_ledger_lists_all_citations():
    md = """## Source quality

- Band / unknown: [1 preprint]

## Limitations

Run limits.
"""
    citations = [
        {"n": 1, "url": "https://arxiv.org/abs/111", "tier": "peer_reviewed"},
        {"n": 2, "url": "https://arxiv.org/abs/222", "tier": "peer_reviewed"},
        {"n": 7, "url": "https://doi.org/10.1000/peer", "tier": "peer_reviewed"},
        {"n": 8, "url": "https://doi.org/10.1000/peer2", "tier": "peer_reviewed"},
        {"n": 11, "url": "https://pubmed.ncbi.nlm.nih.gov/1", "tier": "specialist_research"},
    ]
    evidence = [
        {"url": c["url"], "tier": c["tier"], "title": f"Source {c['n']}"} for c in citations
    ]
    out = apply_source_quality_from_ledger(md, citations, evidence)
    sq = out.split("## Limitations")[0]
    assert "[1, 2 preprint]" in sq
    assert "[7, 8 peer]" in sq
    assert "[11 specialist]" in sq
    assert "Band / unknown" not in sq
