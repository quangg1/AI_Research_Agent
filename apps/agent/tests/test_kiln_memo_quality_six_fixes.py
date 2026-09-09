# -*- coding: utf-8 -*-
"""Regression tests for the six Kiln memo-quality fixes."""

from __future__ import annotations

from app.domain.citations import format_source_quality_section, inline_tier_label
from app.domain.constraint_audit import (
    audit_memo_against_contract,
    audit_note_to_measurement_gap_prose,
)
from app.domain.number_provenance import (
    _first_author,
    _rewrite_false_attribution,
    polish_number_provenance,
)
from app.domain.offtopic_domains import evidence_is_har_sensor_ood
from app.domain.report_integrity import (
    enforce_report_integrity,
    harden_unverified_numeric_claims,
)
from app.domain.research_contract import (
    ResearchContract,
    topic_relevance_score,
)
from app.domain.research_intent import flip_condition_for
from app.report.memo_structure import consolidate_memo_structure


# --- Fix 1: measured evidence 0% → vendor-reported labels --------------------

def test_harden_labels_hard_numbers_when_quant_absent():
    body = (
        "## Key findings\n\n"
        "- Peak accuracy reaches 76% on the vendor suite [4].\n\n"
        "## Comparison\n\n"
        "| Method | Score |\n| --- | --- |\n| A | 12.5 GB |\n\n"
        "## Decision rule\n\n"
        "- Prefer path A when latency is under 40ms.\n\n"
        "## Quantitative findings\n\n"
        "Measurements absent in collected sources.\n"
    )
    out, flags = harden_unverified_numeric_claims(body, quant_absent=True)
    assert "vendor_reported_numbers_hardened" in flags
    assert "76% (vendor-reported / not independently verified)" in out
    assert "12.5 GB (vendor-reported / not independently verified)" in out
    assert "40ms (vendor-reported / not independently verified)" in out


def test_enforce_hardens_when_measured_evidence_zero():
    body = (
        "## Key findings\n\nQLoRA saves 50% VRAM vs BF16 [2].\n\n"
        "## Quantitative findings\n\nNo measured numeric results could be verified.\n\n"
        "## Decision rule\n\n- Use QLoRA under 24 GB.\n"
    )
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="QLoRA is cheaper.",
        decision_rule="- Use QLoRA under 24 GB.",
        at_a_glance="Use QLoRA.",
        citations=[{"n": 2, "url": "https://example.com/blog", "tier": "vendor_or_consultancy"}],
        critic={
            "depth_score": {
                "score": 70,
                "label": "standard",
                "quantitative_evidence": {"pct": 40},
                "breakdown": {},
            }
        },
        limitations=[],
        evidence=[],
        query="LoRA vs QLoRA VRAM for 7B",
    )
    assert out["confidence_breakdown"]["quantitative_evidence_pct"] == 0
    assert "vendor-reported / not independently verified" in out["body_markdown"]


# --- Fix 2: Estimate author stopword / cited source -------------------------

def test_first_author_rejects_english_stopwords():
    assert _first_author("As reported by Dettmers et al., peak is 5 GB.") == "Dettmers et al."
    assert _first_author("Estimate reports 5 GB on the suite.") is None
    assert _first_author("When measures 12 GB peak memory.") is None
    assert _first_author("Benchmark reports 3.5 GB for 7B.") is None


def test_rewrite_falls_back_to_cited_source_without_author():
    line = "As reported by As, peak memory is 28 GB [1]."
    # Invalid author token "As" → cited source phrasing
    out = _rewrite_false_attribution(line, kind="multi_source_estimate", author="As")
    assert "the cited source" in out
    assert "from As)" not in out
    assert "from As," not in out


def test_polish_estimate_label_uses_cited_source_not_stopword():
    body = (
        "## Key findings\n\n"
        "As reported by Estimate, QLoRA peak memory is 28 GB [1].\n"
    )
    out = polish_number_provenance(
        body,
        citations=[{"n": 1, "url": "https://arxiv.org/abs/2305.14314"}],
        evidence=[{
            "url": "https://arxiv.org/abs/2305.14314",
            "full_text": "QLoRA fine-tuning a 7B model requires approximately 5 GB of GPU memory.",
        }],
    )
    assert "the cited source" in out["body_markdown"]
    assert "from Estimate" not in out["body_markdown"]


# --- Fix 3: HAR/sensor demoted at ranking before extract --------------------

def test_har_sensor_ood_blocked_for_llm_ft_query():
    q = "LoRA vs QLoRA peak VRAM for 7B LLM fine-tuning"
    har = {
        "title": "Masked Autoencoder for Human Activity Recognition",
        "snippet": "HHAR accelerometer gyroscope wearable sensor activity recognition accuracy 94%.",
        "quote": "On the HHAR benchmark our HAR backbone reaches 94.2% activity recognition.",
    }
    lora = {
        "title": "QLoRA: Efficient Finetuning of Quantized LLMs",
        "snippet": "QLoRA fine-tuning a 7B model uses about 5 GB VRAM with NF4.",
        "quote": "QLoRA reduces memory for LLM fine-tuning.",
    }
    assert evidence_is_har_sensor_ood(har, q) is True
    assert evidence_is_har_sensor_ood(lora, q) is False
    assert topic_relevance_score(har, q) == 0.0
    assert topic_relevance_score(lora, q) > 0.0


def test_hhar_table_still_dropped_in_structure_pass():
    md = (
        "# Memo\n\nLoRA and QLoRA fine-tuning for LLMs.\n\n"
        "## Quantitative findings\n\n"
        "| Metric | Value | Source |\n| --- | --- | --- |\n"
        "| HHAR accuracy | 94.2% | [3] |\n"
    )
    out = consolidate_memo_structure(md)
    assert "HHAR accuracy" not in out


# --- Fix 4: constraint_audit → Measurement gaps prose -----------------------

def test_audit_flags_rewritten_to_measurement_gap_voice():
    prose = audit_note_to_measurement_gap_prose("mandatory_missing_2305.14314")
    assert "_" not in prose or "2305" in prose
    assert "mandatory_missing" not in prose
    assert prose.startswith("Open measurement gap") or "Primary reference" in prose

    prose2 = audit_note_to_measurement_gap_prose(
        "Mandatory source missing from citations/evidence: Dettmers et al. QLoRA (2305.14314)."
    )
    assert "Primary reference not yet in the citation ledger" in prose2
    assert "Mandatory source missing from citations/evidence" not in prose2


def test_constraint_audit_inserts_prose_not_raw_flags():
    contract = ResearchContract(
        query="LoRA vs QLoRA 7B VRAM",
        excluded_domains=["clinical"],
        mandatory_sources=[{
            "arxiv_id": "2305.14314",
            "label": "Dettmers et al. QLoRA",
            "role": "qlora_primary",
        }],
        scale_bounds=["7B"],
    )
    body = (
        "## Key findings\n\nClinical text abstention accuracy improved.\n\n"
        "## References\n\n- **[1]** Other\n"
    )
    out = audit_memo_against_contract(
        body,
        contract,
        query=contract.query,
        citations=[{"n": 1, "url": "https://example.com/x"}],
        evidence=[],
    )
    unc = out["body_markdown"]
    assert "## Uncertainties" in unc or "Uncertainties" in unc
    assert "mandatory_missing_" not in unc
    assert "Primary reference not yet" in unc or "Open measurement gap" in unc or "out of scope" in unc.lower()


# --- Fix 5: REVISIT IF only gets revisit conditions -------------------------

def test_flip_condition_is_revisit_only_not_adapter_bullet():
    text = flip_condition_for(
        "LoRA vs QLoRA for 7B adapters",
        {"coverage": {"slots": [
            {"id": "adapter_choice", "label": "locking adapter choice", "status": "open"},
        ]}},
    )
    assert text.lower().startswith("revisit if")
    assert "verify:" not in text.lower()
    assert "act on" not in text.lower()


def test_decision_rule_includes_revisit_if_line():
    from app.domain.research_intent import decision_rule_for

    rule = decision_rule_for(
        "LoRA vs QLoRA VRAM",
        ledger=[{"n": 1}],
        critic={"coverage": {"slots": []}},
    )
    assert "Revisit if:" in rule


# --- Fix 6: inline tier matches Source quality vocabulary -------------------

def test_source_quality_uses_same_inline_tier_as_markers():
    citations = [
        {
            "n": 4,
            "url": "https://github.com/artidoro/qlora",
            "title": "QLoRA repo",
            "tier": "specialist_research",
        }
    ]
    assert inline_tier_label(citations[0]) == "repo"
    section = format_source_quality_section(citations)
    assert "[4 repo]" in section
    assert "Code Repositories" in section
    # Must not list the same citation as specialist while inline says repo.
    assert "4 specialist" not in section
