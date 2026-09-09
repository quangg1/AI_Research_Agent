"""Small relevance-first ranking tests (Kiln Phase A). Standalone."""

from __future__ import annotations

from app.domain.adversarial import retrieval_rank_score
from app.domain.citations import build_ledger
from app.domain.research_contract import compile_research_contract, topic_relevance_score


def test_retrieval_rank_score_prefers_topic_over_high_cred_offtopic():
    q = "LoRA vs QLoRA peak VRAM memory footprint on 7B"
    topical_low_cred = {
        "title": "QLoRA memory footprint for 7B LLaMA",
        "snippet": "Peak VRAM for QLoRA 7B with NF4 is about 8-10 GB.",
        "url": "https://arxiv.org/abs/2305.14314",
        "credibility": 0.2,
        "tier": "unknown",
    }
    famous_offtopic = {
        "title": "A landmark peer-reviewed study of coral reef biodiversity",
        "snippet": "We survey reef ecosystems across three oceans with novel transects.",
        "url": "https://doi.org/10.1000/reef",
        "credibility": 0.99,
        "tier": "peer_reviewed",
    }
    assert topic_relevance_score(topical_low_cred, q) > topic_relevance_score(famous_offtopic, q)
    assert retrieval_rank_score(topical_low_cred, q) > retrieval_rank_score(famous_offtopic, q)


def test_build_ledger_orders_by_relevance_then_authority():
    q = "LoRA vs QLoRA peak VRAM on 7B models"
    contract = compile_research_contract(q, {}).to_dict()
    evidence = [
        {
            "id": "reef",
            "title": "Coral reef biodiversity survey",
            "url": "https://arxiv.org/abs/1111.11111",
            "snippet": "Reef transect biodiversity indices.",
            "tier": "peer_reviewed",
            "credibility": 0.99,
        },
        {
            "id": "qlora",
            "title": "QLoRA: Efficient Finetuning of Quantized LLMs",
            "url": "https://arxiv.org/abs/2305.14314",
            "snippet": "7B QLoRA peak VRAM with 4-bit NF4 and paged optimizers.",
            "full_text": "LLaMA 7B QLoRA peak training VRAM roughly 8-10 GB versus LoRA ~20 GB.",
            "tier": "specialist_research",
            "credibility": 0.5,
        },
        {
            "id": "pref",
            "title": "DPO ORPO KTO for mental-health clinical LoRA",
            "url": "https://arxiv.org/abs/2604.00773",
            "snippet": "Preference optimization on clinical text; no VRAM reported.",
            "full_text": "DPO ORPO KTO mental-health clinical dialogue. No GPU VRAM measured.",
            "tier": "specialist_research",
            "credibility": 0.8,
        },
    ]
    ledger = build_ledger(evidence, k=5, query=q, contract=contract)
    ids = [c.evidence_id for c in ledger]
    assert "qlora" in ids
    # Off-topic / excluded should not outrank the QLoRA primary when present.
    if "reef" in ids and "qlora" in ids:
        assert ids.index("qlora") < ids.index("reef")
    assert "pref" not in ids
