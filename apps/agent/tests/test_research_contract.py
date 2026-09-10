"""Standalone tests for ResearchContract (Kiln Phase A). No graph/psycopg."""

from __future__ import annotations

from app.domain.research_contract import (
    compile_research_contract,
    evidence_fails_scope_assessors,
    filter_evidence_for_contract,
    mandatory_sources_present,
    topic_relevance_score,
)

QUERY_7B = (
    "Peak VRAM / training memory footprints of LoRA vs QLoRA on 7B models; "
    "prefer primary sources (Hu LoRA, Dettmers QLoRA)."
)


def test_compile_memory_contract_has_excludes_scales_mandatory():
    brief = {
        "goal": QUERY_7B,
        "must_cover": ["peak VRAM LoRA", "peak VRAM QLoRA"],
        "constraints": ["Prefer primary papers"],
        "out_of_scope": ["DPO preference papers"],
    }
    c = compile_research_contract(QUERY_7B, brief)
    d = c.to_dict()
    assert "7b" in d["scale_bounds"]
    assert any(x.upper() == "DPO" or x == "DPO" for x in d["excluded_domains"])
    assert any(x in {"ORPO", "KTO", "clinical", "email-QA", "abstention"} for x in d["excluded_domains"])
    aids = {s["arxiv_id"] for s in d["mandatory_sources"]}
    assert "2106.09685" in aids
    assert "2305.14314" in aids
    assert d["must_cover"]
    assert d["authority_policy"].get("repos")
    assert d["raw_constraints"]


def test_topic_relevance_ranks_on_topic_above_off_topic():
    q = "LoRA vs QLoRA peak VRAM on 7B models"
    on = {
        "title": "QLoRA: Efficient Finetuning of Quantized LLMs",
        "snippet": "We measure peak VRAM for LLaMA 7B with 4-bit NF4 QLoRA.",
        "url": "https://arxiv.org/abs/2305.14314",
        "credibility": 0.4,
        "tier": "specialist_research",
    }
    off = {
        "title": "Soccer workload analytics for premier league recovery",
        "snippet": "Athlete GPS load and green-banking credit scores.",
        "url": "https://example.com/soccer",
        "credibility": 0.95,
        "tier": "peer_reviewed",
    }
    assert topic_relevance_score(on, q) > topic_relevance_score(off, q)
    assert topic_relevance_score(off, q) == 0.0


def test_pre_cite_filter_drops_preference_clinical_without_vram():
    c = compile_research_contract(QUERY_7B, {"must_cover": ["VRAM"]})
    bad = {
        "id": "pref",
        "title": "DPO ORPO KTO LoRA for mental-health clinical text",
        "snippet": (
            "Preference optimization with DPO/ORPO/KTO on clinical dialogue. "
            "No GPU VRAM or peak training memory footprints are reported."
        ),
        "full_text": (
            "We fine-tune with LoRA and QLoRA for mental-health text using DPO, ORPO, and KTO. "
            "Class-rebalanced preference adaptation improves F1. No VRAM measured."
        ),
        "url": "https://arxiv.org/abs/2604.00773",
    }
    good = {
        "id": "qlora",
        "title": "QLoRA: Efficient Finetuning of Quantized LLMs",
        "snippet": "7B QLoRA peak training VRAM fits in roughly 8-10 GB with NF4.",
        "full_text": (
            "QLoRA on LLaMA 7B with 4-bit NF4. Base weight memory ~3.5 GB; "
            "peak training VRAM around 8-10 GB versus ~20 GB for 16-bit LoRA."
        ),
        "url": "https://arxiv.org/abs/2305.14314",
    }
    assert evidence_fails_scope_assessors(bad, QUERY_7B, c) is True
    assert evidence_fails_scope_assessors(good, QUERY_7B, c) is False
    kept = filter_evidence_for_contract([bad, good], QUERY_7B, c)
    assert any(e.get("id") == "qlora" for e in kept)
    assert not any(e.get("id") == "pref" for e in kept)


def test_pre_cite_filter_drops_har_sensor_paper():
    """HAR/wearable-sensor papers must not enter the ledger/dossier for an
    LLM fine-tuning query — topic_relevance_score already ranked them last,
    but a ranked-last source still got cited when nothing else scored
    higher (real run: HAR paper cited repeatedly as [1] for LoRA/QLoRA
    hyperparameter and accuracy claims)."""
    har = {
        "id": "har",
        "title": "Parameter-Efficient Fine-Tuning for HAR: Integrating LoRA and QLoRA into Transformer Models",
        "snippet": "We apply LoRA and QLoRA to human activity recognition (HAR) using wearable accelerometer and gyroscope sensor data.",
        "full_text": (
            "Human activity recognition (HAR) from wearable sensor accelerometer and gyroscope streams. "
            "LoRA and QLoRA adapters reduce trainable parameters for the HAR classification head."
        ),
        "url": "https://arxiv.org/html/2512.17983v1",
    }
    good = {
        "id": "qlora",
        "title": "QLoRA: Efficient Finetuning of Quantized LLMs",
        "snippet": "7B QLoRA peak training VRAM fits in roughly 8-10 GB with NF4.",
        "full_text": "QLoRA on LLaMA 7B with 4-bit NF4. Peak training VRAM around 8-10 GB.",
        "url": "https://arxiv.org/abs/2305.14314",
    }
    assert evidence_fails_scope_assessors(har, QUERY_7B) is True
    kept = filter_evidence_for_contract([har, good], QUERY_7B)
    assert not any(e.get("id") == "har" for e in kept)
    assert any(e.get("id") == "qlora" for e in kept)


def test_mandatory_sources_detection():
    c = compile_research_contract(QUERY_7B, {})
    missing = mandatory_sources_present(c, citations=[], evidence=[])
    assert len(missing) == 2
    evidence = [
        {"url": "https://arxiv.org/abs/2305.14314", "title": "QLoRA Dettmers"},
        {"url": "https://arxiv.org/abs/2106.09685", "title": "LoRA Hu"},
    ]
    assert mandatory_sources_present(c, evidence=evidence) == []
