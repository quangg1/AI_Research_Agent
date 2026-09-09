"""Subject/scope claim↔evidence gates — live memo ff3c5688 failure modes.

Standalone: imports metric_grounding / verify_citations / report_integrity only
(no graph/nodes/psycopg). Fixtures mirror [4] 1.5B scale bleed, [5] preference/
clinical LoRA, [7] abstention QA, plus a good QLoRA 7B primary.
"""

from __future__ import annotations

from app.domain.metric_grounding import (
    assess_subject_scale_scope,
    assess_subject_topic_scope,
    audit_memo_subject_scope,
    demote_off_scope_memo_content,
    extract_model_scales,
    is_memory_footprint_query,
)
from app.domain.report_integrity import enforce_report_integrity
from app.domain.verify_citations import verify_against_sources

QUERY_7B_VRAM = (
    "Peak VRAM / training memory footprints of LoRA vs QLoRA on 7B models; "
    "prefer primary sources (Hu LoRA, Dettmers QLoRA)."
)

# [4] arXiv 2509.12229-style: Qwen2.5-1.5B on RTX 4060 — NOT 7B.
SOURCE_1P5B_PROFILE = (
    "We profile Qwen2.5-1.5B fine-tuning with QLoRA on an RTX 4060. "
    "With 4-bit NF4 and a paged optimizer, peak VRAM is 8,062 MB at sequence "
    "length 1024 and batch size 2 using FP16 compute (628 tokens/s). "
    "Switching compute precision to BF16 drops throughput to 360 tokens/s "
    "due to paged optimizer and dequantization overhead, not because BF16 "
    "is measured only for this 1.5B setup."
)

# [5] arXiv 2604.00773-style: LoRA/QLoRA + DPO/ORPO/KTO for mental-health text.
SOURCE_PREFERENCE_CLINICAL = (
    "We fine-tune instruction models with LoRA and QLoRA for mental-health "
    "text classification using Direct Preference Optimization (DPO), Odds Ratio "
    "Preference Optimization (ORPO), and Kahneman-Tversky Optimization (KTO). "
    "Class-rebalanced preference adaptation improves minority-class F1 on "
    "clinical dialogue benchmarks. Preference-based optimization dynamics are "
    "analyzed across chosen vs rejected response pairs. No GPU VRAM or peak "
    "training memory footprints are reported for 7B LoRA vs QLoRA comparison."
)

# [7] MDPI-style email QA abstention paper.
SOURCE_ABSTENTION = (
    "On email question answering, unanswerable-aware supervision restores "
    "abstention accuracy to approximately 0.998. The study evaluates whether "
    "models abstain on unanswerable email QA items. Parameter-efficient "
    "adapters are mentioned only as a training convenience; the paper does not "
    "measure VRAM, GPU memory, or LoRA vs QLoRA memory footprints."
)

# Good primary: QLoRA 7B memory evidence (Dettmers-style).
SOURCE_QLORA_7B_PRIMARY = (
    "QLoRA: Efficient Finetuning of Quantized LLMs. We fine-tune LLaMA 7B with "
    "4-bit NormalFloat (NF4) QLoRA. Base model weight memory drops from about "
    "14 GB in 16-bit to about 3.5 GB in 4-bit NF4. With double quantization and "
    "paged optimizers, peak training VRAM for 7B QLoRA fits in roughly 8-10 GB "
    "under standard sequence lengths, versus ~20 GB for 16-bit LoRA on the same "
    "7B model."
)


def test_extract_and_detect_memory_query():
    assert "7b" in extract_model_scales(QUERY_7B_VRAM)
    assert "1.5b" in extract_model_scales(SOURCE_1P5B_PROFILE)
    assert is_memory_footprint_query(QUERY_7B_VRAM)


def test_scale_mismatch_1p5b_numbers_attributed_to_7b():
    claim = (
        "For 7B fine-tuning on consumer GPUs, QLoRA with FP16 compute reaches "
        "628 tokens/s while BF16 drops to 360 tokens/s at 8,062 MB peak VRAM."
    )
    result = assess_subject_scale_scope(claim, SOURCE_1P5B_PROFILE, query=QUERY_7B_VRAM)
    assert result is not None
    assert result["status"] == "scale_mismatch"
    assert "1.5" in result["note"] or "1.5b" in str(result.get("source_scales"))


def test_scale_ok_when_source_is_7b_primary():
    claim = (
        "QLoRA reduces 7B static weight memory from ~14 GB (16-bit) to ~3.5 GB "
        "(4-bit NF4), with peak training VRAM around 8-10 GB."
    )
    result = assess_subject_scale_scope(claim, SOURCE_QLORA_7B_PRIMARY, query=QUERY_7B_VRAM)
    assert result is None


def test_topic_mismatch_preference_clinical_cannot_drive_vram_claim():
    claim = (
        "QLoRA lowers peak VRAM for 7B LoRA fine-tuning to the 8-10 GB range "
        "while preserving adapter quality."
    )
    result = assess_subject_topic_scope(
        claim, SOURCE_PREFERENCE_CLINICAL, query=QUERY_7B_VRAM
    )
    assert result is not None
    assert result["status"] == "topic_mismatch"


def test_topic_mismatch_preference_section_claim():
    claim = (
        "Class-rebalanced preference adaptation with DPO/ORPO/KTO maintains "
        "relative task performance under LoRA vs QLoRA."
    )
    result = assess_subject_topic_scope(
        claim, SOURCE_PREFERENCE_CLINICAL, query=QUERY_7B_VRAM
    )
    assert result is not None
    assert result["status"] == "topic_mismatch"


def test_topic_mismatch_abstention_accuracy_not_vram():
    claim = (
        "Memory-efficient adapters restore abstention accuracy to approximately "
        "0.998, indicating a favorable VRAM tradeoff for 7B QLoRA."
    )
    result = assess_subject_topic_scope(claim, SOURCE_ABSTENTION, query=QUERY_7B_VRAM)
    assert result is not None
    assert result["status"] == "topic_mismatch"


def test_topic_ok_for_dettmers_style_primary():
    claim = (
        "On 7B models, QLoRA 4-bit NF4 cuts base weight memory from ~14 GB to "
        "~3.5 GB versus 16-bit LoRA."
    )
    result = assess_subject_topic_scope(
        claim, SOURCE_QLORA_7B_PRIMARY, query=QUERY_7B_VRAM
    )
    assert result is None


def test_verifier_flags_scale_and_topic_mismatches():
    evidence = [
        {
            "id": "e4",
            "url": "https://arxiv.org/abs/2509.12229",
            "title": "Qwen2.5-1.5B RTX 4060 profile",
            "full_text": SOURCE_1P5B_PROFILE,
            "tier": "specialist_research",
        },
        {
            "id": "e5",
            "url": "https://arxiv.org/abs/2604.00773",
            "title": "LoRA QLoRA DPO mental health",
            "full_text": SOURCE_PREFERENCE_CLINICAL,
            "tier": "specialist_research",
        },
        {
            "id": "e7",
            "url": "https://doi.org/10.3390/electronics13010001",
            "title": "Email QA abstention",
            "full_text": SOURCE_ABSTENTION,
            "tier": "peer_reviewed",
        },
        {
            "id": "e1",
            "url": "https://arxiv.org/abs/2305.14314",
            "title": "QLoRA Efficient Finetuning",
            "full_text": SOURCE_QLORA_7B_PRIMARY,
            "tier": "specialist_research",
        },
    ]
    citations = [
        {"n": 4, "url": evidence[0]["url"]},
        {"n": 5, "url": evidence[1]["url"]},
        {"n": 7, "url": evidence[2]["url"]},
        {"n": 1, "url": evidence[3]["url"]},
    ]
    claims = [
        {
            "id": "C4",
            "text": (
                "7B QLoRA on consumer GPUs achieves 628 tokens/s (FP16) vs "
                "360 tokens/s (BF16) at 8062 MB peak VRAM."
            ),
            "quote": "628 tokens/s",
            "url": evidence[0]["url"],
            "kind": "direct",
            "confidence": 0.9,
        },
        {
            "id": "C5",
            "text": (
                "Class-rebalanced preference adaptation with DPO/ORPO/KTO "
                "preserves LoRA vs QLoRA relative performance."
            ),
            "quote": "Class-rebalanced preference adaptation",
            "url": evidence[1]["url"],
            "kind": "direct",
            "confidence": 0.9,
        },
        {
            "id": "C7",
            "text": (
                "Unanswerable-aware supervision restored abstention accuracy "
                "to approximately 0.998 under memory-efficient adapters."
            ),
            "quote": "abstention accuracy to approximately 0.998",
            "url": evidence[2]["url"],
            "kind": "direct",
            "confidence": 0.9,
        },
        {
            "id": "C1",
            "text": (
                "QLoRA on 7B reduces base weight memory from ~14 GB to ~3.5 GB "
                "with peak training VRAM around 8-10 GB."
            ),
            "quote": "peak training VRAM for 7B QLoRA fits in roughly 8-10 GB",
            "url": evidence[3]["url"],
            "kind": "direct",
            "confidence": 0.9,
        },
    ]
    graph = verify_against_sources(
        claims, evidence, citations, refetch=False, query=QUERY_7B_VRAM
    )
    by_id = {c["id"]: c for c in graph["claims"]}
    assert by_id["C4"]["verification_status"] == "wrong_causal"
    assert by_id["C5"]["verification_status"] == "unsupported"
    assert by_id["C7"]["verification_status"] == "unsupported"
    assert by_id["C1"]["verification_status"] == "verified"


def test_demote_strips_off_scope_sections_without_blanking_refs():
    body = """## Key findings
1. **Throughput**: 7B QLoRA reaches 628 tokens/s FP16 vs 360 BF16 at 8062 MB [4].
2. **Primary**: QLoRA cuts 7B weight memory from ~14 GB to ~3.5 GB [1].

## Detailed analysis

### Class-rebalanced preference adaptation
Class-rebalanced DPO/ORPO/KTO protocols preserve relative task performance [5].

### Direct VRAM comparison for 7B models
QLoRA on 7B reduces static weights to ~3.5 GB [1].

### Preference-based optimization dynamics
Preference objectives double batch sequence length; abstention accuracy ~0.998 [5], [7].

## References
- **[1]** Dettmers et al. QLoRA. https://arxiv.org/abs/2305.14314
- **[4]** 1.5B profile. https://arxiv.org/abs/2509.12229
- **[5]** Mental-health preference. https://arxiv.org/abs/2604.00773
- **[7]** Email QA abstention. https://doi.org/10.3390/electronics13010001
"""
    evidence = [
        {"url": "https://arxiv.org/abs/2509.12229", "full_text": SOURCE_1P5B_PROFILE},
        {"url": "https://arxiv.org/abs/2604.00773", "full_text": SOURCE_PREFERENCE_CLINICAL},
        {"url": "https://doi.org/10.3390/electronics13010001", "full_text": SOURCE_ABSTENTION},
        {"url": "https://arxiv.org/abs/2305.14314", "full_text": SOURCE_QLORA_7B_PRIMARY},
    ]
    citations = [
        {"n": 4, "url": evidence[0]["url"], "title": "1.5B"},
        {"n": 5, "url": evidence[1]["url"], "title": "pref"},
        {"n": 7, "url": evidence[2]["url"], "title": "abstain"},
        {"n": 1, "url": evidence[3]["url"], "title": "QLoRA"},
    ]
    demoted, flags = demote_off_scope_memo_content(
        body, query=QUERY_7B_VRAM, citations=citations, evidence=evidence
    )
    assert flags, "expected demotion flags"
    assert "Demoted (off-scope for query)" in demoted
    assert "Class-rebalanced DPO/ORPO/KTO protocols preserve" not in demoted
    # Good primary line kept.
    assert "3.5 GB" in demoted
    # References markers must remain (no ****) — demotion must not swallow ledger.
    assert "## References" in demoted
    assert "**[1]**" in demoted
    assert "**[4]**" in demoted
    assert "**[5]**" in demoted
    assert "**[7]**" in demoted
    assert "****" not in demoted
    # Scale-mismatched Key findings line stripped.
    assert "628 tokens/s" not in demoted


def test_enforce_integrity_demotes_without_star_star_star_star():
    body = """## Executive summary
7B QLoRA memory vs LoRA [1].

## Key findings
1. 7B QLoRA throughput 628 tokens/s at 8062 MB [4].
2. 7B weight memory ~3.5 GB under QLoRA [1].

## Detailed analysis

### Class-rebalanced preference adaptation
Preference adaptation with DPO [5].

### Direct VRAM comparison for 7B models
QLoRA 7B static weights ~3.5 GB [1].

## Decision rule
1. Prefer QLoRA when 7B VRAM headroom is under 12 GB [1].

## Source quality
- **[1]** primary
- **[4]** preprint
- **[5]** preprint

## References
- **[1]** QLoRA https://arxiv.org/abs/2305.14314
- **[4]** 1.5B https://arxiv.org/abs/2509.12229
- **[5]** pref https://arxiv.org/abs/2604.00773
"""
    evidence = [
        {"url": "https://arxiv.org/abs/2509.12229", "full_text": SOURCE_1P5B_PROFILE, "title": "1.5B"},
        {"url": "https://arxiv.org/abs/2604.00773", "full_text": SOURCE_PREFERENCE_CLINICAL, "title": "pref"},
        {"url": "https://arxiv.org/abs/2305.14314", "full_text": SOURCE_QLORA_7B_PRIMARY, "title": "QLoRA"},
    ]
    citations = [
        {"n": 1, "url": evidence[2]["url"], "title": "QLoRA"},
        {"n": 4, "url": evidence[0]["url"], "title": "1.5B"},
        {"n": 5, "url": evidence[1]["url"], "title": "pref"},
    ]
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="7B QLoRA vs LoRA memory.",
        decision_rule="1. Prefer QLoRA under 12 GB [1].",
        at_a_glance="Prefer QLoRA for 7B memory.",
        citations=citations,
        critic={"depth_score": {"score": 70, "label": "standard", "breakdown": {}}},
        limitations=[],
        evidence=evidence,
        query=QUERY_7B_VRAM,
    )
    md = out["body_markdown"]
    assert "****" not in md
    # Good primary survives; off-scope preference subsection demoted.
    assert "**[1]**" in md or "[1]" in md
    assert "Demoted (off-scope for query)" in md
    assert "Preference adaptation with DPO" not in md
    assert any("off_scope" in f for f in out["integrity_flags"])
    # Scale-mismatched 628 tok/s line must not remain as a 7B attribution.
    assert "628 tokens/s" not in md
    notes = audit_memo_subject_scope(body, query=QUERY_7B_VRAM)
    assert notes

