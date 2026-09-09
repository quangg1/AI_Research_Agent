from app.domain.evidence_pipeline import (
    compute_evidence_pipeline_stats,
    quant_signal_level,
    quant_signal_score,
)
from app.domain.quantitative_verify import semantic_number_grounded, verify_quantitative_row
from app.graph.state import merge_unique_evidence


def test_quant_signal_high_on_measured_snippet():
    ev = {
        "search_snippet": "Our method improves accuracy by 4.2% over the baseline on MMLU.",
        "url": "https://arxiv.org/abs/1",
    }
    assert quant_signal_level(ev) == "high"
    assert quant_signal_score(ev) >= 2.0


def test_quant_signal_low_on_prose_only():
    ev = {"search_snippet": "We introduce a novel architecture for synthetic data generation."}
    assert quant_signal_level(ev) == "low"


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


def test_pipeline_stats_distinguish_inaccessible_vs_budget():
    evidence = [
        {"url": "https://doi.org/10.1/a", "snippet": "title only", "title": "Paper A"},
        {
            "url": "https://arxiv.org/abs/2",
            "search_snippet": "accuracy 71.2% on SWE-bench",
            "full_text": "Results: accuracy 71.2% on SWE-bench Verified.",
        },
    ]
    citations = [{"n": 1, "url": e["url"]} for e in evidence]
    stats = compute_evidence_pipeline_stats(
        evidence,
        citations,
        gate_reason="insufficient_budget",
        verified_quant=1,
    )
    assert stats["retrieved"] == 2
    assert stats["fetched"] == 1
    assert stats["termination"] == "budget_exhausted"
    assert "identified 2 relevant sources" in stats["summary"] or "2 cited ·" in stats["summary"]


def test_pipeline_budget_summary_uses_clear_counters():
    from app.domain.evidence_pipeline import pipeline_summary

    text = pipeline_summary(
        retrieved=12,
        fetched=11,
        accessible=16,
        quant_candidates=17,
        verified_quant=0,
        termination="budget_exhausted",
    )
    assert "12 cited ·" in text
    assert "11 deep-fetched ·" in text
    assert "16 with excerpt ·" in text
    assert "17 numeric mentions ·" in text
    assert "0 verified" in text


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
