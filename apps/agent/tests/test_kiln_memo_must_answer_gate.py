# -*- coding: utf-8 -*-
"""Standalone tests: LoRA/FT must-answer from contract, mandatory hard-fail, slot-leak strip."""

from __future__ import annotations

from app.domain.constraint_audit import audit_memo_against_contract
from app.domain.coverage import must_answer_for
from app.domain.decompose import reset_slot_cache
from app.domain.research_contract import (
    compile_research_contract,
    filter_poison_must_answer_slots,
    is_lora_qlora_ft_query,
    is_poison_critical_slot,
    must_answer_from_contract,
)
from app.report.memo_structure import consolidate_memo_structure, _drop_slot_label_decision_bullets


QUERY_LORA_FT = (
    "Empirical trade-offs of full fine-tuning vs LoRA vs QLoRA for LLMs: "
    "task accuracy, compute cost, peak VRAM, and OOD generalization / catastrophic forgetting."
)

QUERY_VRAM = (
    "Peak VRAM / training memory footprints of LoRA vs QLoRA on 7B models; "
    "prefer primary sources (Hu LoRA, Dettmers QLoRA)."
)

POISON_IDS = {
    "preference-based",
    "preference_based",
    "class-rebalanced",
    "class_rebalanced",
    "self-supervised",
    "self_supervised",
}


def test_lora_ft_query_must_answer_excludes_poison_critical():
    reset_slot_cache()
    assert is_lora_qlora_ft_query(QUERY_LORA_FT)
    contract = compile_research_contract(QUERY_LORA_FT, {"must_cover": ["accuracy", "VRAM", "cost", "OOD"]})
    slots = must_answer_from_contract(QUERY_LORA_FT, contract)
    assert slots, "expected contract-aligned must-answer slots"
    ids = {str(s.get("id") or "").lower() for s in slots}
    labels = " ".join(str(s.get("label") or "").lower() for s in slots)
    for poison in POISON_IDS:
        assert poison not in ids
        assert poison.replace("_", "-") not in ids
        assert poison.replace("-", " ") not in labels or "preference" not in labels
    critical_ids = {str(s.get("id")) for s in slots if s.get("critical")}
    for poison in POISON_IDS:
        assert poison not in critical_ids
        assert poison.replace("-", "_") not in critical_ids
    # Expected FT dimensions present as critical (or at least present)
    for need in ("task_accuracy", "vram_memory", "compute_cost", "ood_generalization", "quantization_tradeoffs"):
        assert need in ids
        assert any(s.get("id") == need and s.get("critical") for s in slots)

    # must_answer_for wiring
    via_coverage = must_answer_for(QUERY_LORA_FT, brief={"research_contract": contract.to_dict()})
    via_ids = {str(s.get("id") or "").lower() for s in via_coverage}
    assert not (via_ids & {p.replace("-", "_") for p in POISON_IDS} & via_ids)
    assert "preference-based" not in via_ids and "class-rebalanced" not in via_ids
    assert "self-supervised" not in via_ids


def test_filter_poison_demotes_or_drops_preference_slots():
    slots = [
        {"id": "preference-based", "label": "Preference-based optimization", "critical": True},
        {"id": "class-rebalanced", "label": "Class-rebalanced preference adaptation", "critical": True},
        {"id": "self-supervised", "label": "Self-supervised pretraining", "critical": True},
        {"id": "vram_memory", "label": "VRAM / peak training memory", "critical": True},
    ]
    assert is_poison_critical_slot(slot_id="preference-based", label="Preference-based")
    cleaned = filter_poison_must_answer_slots(slots, QUERY_VRAM)
    ids = {s["id"] for s in cleaned}
    assert "vram_memory" in ids
    assert "preference-based" not in ids
    assert "class-rebalanced" not in ids
    assert "self-supervised" not in ids


def test_mandatory_missing_audit_hard_fail_blocks_publish():
    contract = compile_research_contract(QUERY_VRAM, {"must_cover": ["VRAM"]})
    body = """# Memo

## Key findings

- QLoRA reduces peak VRAM on 7B models.

## Uncertainties & gaps

- TBD
"""
    out = audit_memo_against_contract(
        body,
        contract,
        query=QUERY_VRAM,
        citations=[{"n": 1, "url": "https://example.com/other", "title": "Other"}],
        evidence=[{"url": "https://example.com/other", "title": "Other", "snippet": "no lora"}],
    )
    assert out["had_contract"] is True
    assert out.get("missing_mandatory"), "Hu/Dettmers should be missing"
    assert out.get("hard_fail") is True
    assert out.get("should_block_publish") is True
    assert "hard_fail_mandatory_missing" in (out.get("flags") or [])
    assert "## Uncertainties & gaps" in out["body_markdown"]
    assert any("2106.09685" in g or "Hu" in g for g in out["gaps"])


def test_mandatory_present_no_hard_fail():
    contract = compile_research_contract(QUERY_VRAM, {})
    evidence = [
        {"url": "https://arxiv.org/abs/2305.14314", "title": "QLoRA Dettmers"},
        {"url": "https://arxiv.org/abs/2106.09685", "title": "LoRA Hu"},
    ]
    out = audit_memo_against_contract(
        "## Key findings\n\n- QLoRA VRAM lower than LoRA on 7B.\n",
        contract,
        query=QUERY_VRAM,
        citations=evidence,
        evidence=evidence,
    )
    assert out.get("missing_mandatory") == []
    assert out.get("hard_fail") is False
    assert out.get("should_block_publish") is False


def test_decision_rule_slot_leak_stripped():
    md = """## Decision rule
### Empirical cutoffs (sources only)
**Act on these - the sources support them directly:**
- preference-based [2 primary] - from the 8 cited sources.
- Constraints, Limitations, and Failure Modes - from the 8 cited sources.
- Prefer QLoRA when peak VRAM is under 12 GB [1 primary].
### Engineering heuristics (AI suggestion - not from papers)
**Verify before acting - evidence is indirect or single-sourced:**
- Verify: class-rebalanced [2 primary] - weak/single-sourced; confirm with one independent primary source.
- Verify: self-supervised [1] - weak/single-sourced; confirm with one independent primary source.
- Verify: re-benchmark domain quality before locking adapter choice.
"""
    out = consolidate_memo_structure(md)
    assert "from the 8 cited sources" not in out
    assert "preference-based" not in out.lower()
    assert "class-rebalanced" not in out.lower()
    assert "self-supervised" not in out.lower()
    assert "Prefer QLoRA when peak VRAM" in out
    assert "re-benchmark domain quality" in out

    # Direct helper path used by report._sanitize_decision_rule
    stripped = _drop_slot_label_decision_bullets(md.split("## Decision rule")[-1])
    assert "preference-based" not in stripped.lower()
    assert "from the 8 cited sources" not in stripped


def test_hhar_quant_table_dropped_on_llm_ft_memo():
    md = """# LoRA vs QLoRA fine-tuning VRAM on 7B LLMs

## Executive summary
QLoRA reduces peak VRAM for 7B language model fine-tuning.

## Quantitative findings
| Metric | Value | Source |
|:---|:---|:---|
| HHAR accuracy | 94.2% | [3] |
| Sensor F1 | 0.88 | [3] |
"""
    out = consolidate_memo_structure(md)
    assert "HHAR accuracy" not in out
    assert "off-topic" in out.lower() or "omitted" in out.lower()
