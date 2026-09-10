from app.domain.quantitative_verify import semantic_number_grounded, verify_quantitative_row
from app.graph.state import merge_unique_evidence


def test_merge_preserves_search_snippet_when_full_text_arrives():
    left = [
        {
            "id": "a",
            "url": "https://arxiv.org/abs/123",
            "snippet": "search excerpt about synthetic data benchmarks",
            "search_snippet": "search excerpt about synthetic data benchmarks",
        }
    ]
    right = [
        {
            "id": "a",
            "url": "https://arxiv.org/abs/123",
            "full_text": "x" * 5000 + " accuracy 62% on MMLU",
            "snippet": "fetched head " + "y" * 1500,
        }
    ]
    merged = merge_unique_evidence(left, right)
    assert merged[0]["search_snippet"].startswith("search excerpt")
    assert len(merged[0]["full_text"]) >= 5000


def test_semantic_mismatch_wrong_method_attribution():
    source = "Method A achieved 91.2% accuracy, while Method B achieved 88.5%."
    row = {
        "cells": ["Method B", "91.2%", "[1]"],
        "numbers": ["91.2%"],
    }
    ok, note = semantic_number_grounded(row, source)
    assert not ok
    assert "Method B" in note or "91.2%" in note


def test_semantic_match_when_method_near_number():
    source = "Method B achieved 88.5% accuracy on the dev set."
    row = {"cells": ["Method B", "88.5%"], "numbers": ["88.5%"]}
    ok, _ = semantic_number_grounded(row, source)
    assert ok


def test_verify_row_rejects_semantic_mismatch():
    citations = [{"n": 1, "url": "https://arxiv.org/abs/1"}]
    evidence = [{"url": "https://arxiv.org/abs/1", "full_text": "Method A got 91.2%; Method B got 88.5%."}]
    row = {
        "cells": ["Method B", "91.2%", "[1]"],
        "numbers": ["91.2%"],
        "cite_n": 1,
    }
    result = verify_quantitative_row(row, citations=citations, evidence=evidence)
    assert result["status"] == "semantic_mismatch"
