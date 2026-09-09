"""Regression tests for Kiln memo grounding failures (LoRA memo audit)."""

from app.domain.adversarial import is_predatory_venue, publication_status, quality_band_for
from app.domain.citations import inline_tier_label
from app.domain.credibility import tier_for
from app.domain.metric_grounding import assess_causal_delta, assess_scope_overgeneralization
from app.domain.schema import SourceTier
from app.domain.verify_citations import verify_against_sources


MOTOR_SOURCE = (
    "Experiments show a 96.5% accuracy on single datasets, 91.2% across conditions, "
    "and 82.7% with small samples, outperforming CNN and LSTM models."
)


def test_arxiv_is_not_peer_reviewed_tier():
    assert tier_for("https://arxiv.org/abs/2512.17983") == SourceTier.SPECIALIST_RESEARCH
    assert quality_band_for("https://arxiv.org/abs/2512.17983", "specialist_research") == "B"
    assert publication_status({"url": "https://arxiv.org/abs/2512.17983"}) == "preprint"
    assert inline_tier_label({"url": "https://arxiv.org/abs/2512.17983", "tier": "peer_reviewed"}) == "preprint"


def test_ijsr_predatory_demoted_to_band_c():
    url = "https://doi.org/10.21275/sr24228092614"
    title = "Fine-Tuning Large Language Models with Domain-Specific Data"
    assert is_predatory_venue(url, title)
    assert quality_band_for(url, "peer_reviewed", title) == "C"
    assert publication_status({"url": url, "title": title, "tier": "peer_reviewed"}) == "predatory_or_unreliable"
    assert inline_tier_label({"url": url, "title": title, "tier": "peer_reviewed"}) == "unreliable"


def test_motor_fault_numbers_are_conditions_not_baseline_gain():
    claim = (
        "increased predictive accuracy from an unadapted baseline of 82.7% to 96.5% "
        "(+13.8 percentage point improvement)"
    )
    result = assess_causal_delta(claim, MOTOR_SOURCE)
    assert result is not None
    assert result["status"] == "wrong_causal"


def test_verifier_flags_motor_fault_causal_hallucination():
    evidence = [
        {
            "id": "e12",
            "url": "https://doi.org/10.3390/pr13072051",
            "title": "Motor Fault Diagnosis Qwen2.5-7B",
            "full_text": MOTOR_SOURCE,
            "tier": "peer_reviewed",
        }
    ]
    claims = [
        {
            "id": "C12",
            "text": (
                "Fine-tuned Qwen2.5-7B increased predictive accuracy from an unadapted "
                "baseline of 82.7% to 96.5%."
            ),
            "quote": "96.5% accuracy on single datasets",
            "url": "https://doi.org/10.3390/pr13072051",
            "kind": "direct",
            "confidence": 0.9,
        }
    ]
    graph = verify_against_sources(
        claims, evidence, [{"n": 12, "url": "https://doi.org/10.3390/pr13072051"}], refetch=False
    )
    assert graph["claims"][0]["verification_status"] == "wrong_causal"


def test_har_memory_figures_cannot_be_stated_as_70b_facts():
    claim = (
        "QLoRA reduces static base weight memory by 40% (10.06 MB to 6.22 MB), "
        "enabling 70B models on a single 48GB GPU."
    )
    source = (
        "Table IV reports LoRA frozen params 10.06 MB and QLoRA 6.22 MB on our "
        "Masked Autoencoder HAR backbone (~2.2M parameters). This overhead largely "
        "reflects the limitations of our simplified CPU-based implementation."
    )
    result = assess_scope_overgeneralization(claim, source)
    assert result is not None
    assert result["status"] == "scope_bleed"


def test_verify_uses_evidence_text_field():
    from app.domain.verify_citations import verify_against_sources
    from app.domain.schema import Claim

    claim = Claim(
        id="c1",
        text="Accuracy rose from 82.7% to 96.5% after LoRA on motor fault.",
        quote="82.7%",
        url="https://www.ijsr.net/archive/v12/x.pdf",
        tier="peer_reviewed",
    )
    evidence = [{
        "url": "https://www.ijsr.net/archive/v12/x.pdf",
        "title": "IJSR motor study",
        "tier": "peer_reviewed",
        "text": "On bearing fault diagnosis we observed 82.7% with the unadapted baseline and 96.5% after fine-tuning.",
    }]
    out = verify_against_sources(
        [claim],
        evidence,
        citations=[{"n": 1, "url": evidence[0]["url"], "title": "IJSR"}],
        refetch=False,
    )
    status = out["claims"][0]["verification_status"]
    assert status != "source_missing", out["claims"][0]
    assert out["claims"][0]["quality_band"] in {"C", "D"}


def test_hard_claim_span_gate_rejects_stitched_numbers():
    from app.domain.metric_grounding import assess_claim_span_grounding

    claim = (
        "Accuracy increased from an unadapted baseline of 82.7% to 96.5% after LoRA adaptation."
    )
    # Numbers live in distant sentences with condition language, not a before/after story.
    source = (
        "Under the small-sample evaluation condition the fine-tuned model reached 82.7% accuracy. "
        "Later sections discuss training cost. "
        "Separately, the single-dataset condition scored 96.5% accuracy for the same fine-tuned checkpoint. "
        "Cross-condition transfer was 91.2%."
    )
    out = assess_claim_span_grounding(claim, source)
    assert out is not None
    assert out["status"] == "ungrounded"


def test_hard_claim_span_gate_rejects_mechanism_elaboration():
    from app.domain.metric_grounding import assess_claim_span_grounding

    claim = (
        "LoRA-PAR reached 95.2% retention by dynamically allocating low-rank adapters "
        "based on layer-wise gradient sensitivity."
    )
    source = (
        "Our method retained 95.2% of full fine-tuning performance while updating 0.52% of parameters. "
        "Task routing followed a System 1 / System 2 partition with importance scoring."
    )
    out = assess_claim_span_grounding(claim, source)
    assert out is not None
    assert out["status"] == "ungrounded"
    assert "gradient" in (out.get("note") or "").lower() or "mechanism" in (out.get("note") or "").lower()


def test_hard_claim_span_gate_accepts_aligned_span():
    from app.domain.metric_grounding import assess_claim_span_grounding

    claim = "QLoRA fine-tuning used 0.52% trainable parameters while retaining 95.2% accuracy."
    source = (
        "In our domain-adaptation study, QLoRA fine-tuning used 0.52% trainable parameters "
        "while retaining 95.2% accuracy relative to full fine-tuning."
    )
    out = assess_claim_span_grounding(claim, source)
    assert out is not None
    assert out["status"] == "ok"


def test_verify_marks_ungrounded_numeric_claim_unsupported():
    from app.domain.verify_citations import verify_against_sources
    from app.domain.schema import Claim

    claim = Claim(
        id="c1",
        text=(
            "LoRA-PAR reached 95.2% retention by dynamically allocating adapters "
            "based on layer-wise gradient sensitivity."
        ),
        quote="95.2%",
        url="https://example.org/paper",
        tier="peer_reviewed",
    )
    evidence = [{
        "url": "https://example.org/paper",
        "title": "LoRA-PAR",
        "tier": "peer_reviewed",
        "text": (
            "Our method retained 95.2% of full fine-tuning performance while updating 0.52% of parameters. "
            "Task routing followed System 1 / System 2 importance scoring."
        ),
    }]
    out = verify_against_sources([claim], evidence, refetch=False)
    assert out["claims"][0]["verification_status"] == "unsupported"


def test_semantic_span_rejects_fabricated_prose():
    from app.domain.metric_grounding import assess_claim_span_grounding

    claim = (
        "LoRA-PAR dynamically allocates low-rank adapters based on layer-wise gradient sensitivity "
        "to specialize each transformer block."
    )
    source = (
        "LoRA-PAR partitions tasks using a System 1 / System 2 scheme with importance scoring "
        "and multi-model role-playing to route examples. "
        "Adapters remain static after assignment."
    )
    out = assess_claim_span_grounding(claim, source, kind="direct")
    assert out is not None
    assert out["status"] == "ungrounded"
    assert out.get("mode") == "semantic"


def test_semantic_span_accepts_aligned_prose():
    from app.domain.metric_grounding import assess_claim_span_grounding

    claim = (
        "LoRA-PAR partitions tasks using a System 1 / System 2 scheme with importance scoring."
    )
    source = (
        "We introduce LoRA-PAR, which partitions tasks using a System 1 / System 2 scheme "
        "with importance scoring and multi-model role-playing."
    )
    out = assess_claim_span_grounding(claim, source, kind="direct")
    assert out is not None
    assert out["status"] == "ok"
    assert out.get("mode") == "semantic"


def test_semantic_span_skips_inferred_kind():
    from app.domain.metric_grounding import assess_claim_span_grounding

    claim = "Therefore teams should prefer LoRA for most production fine-tunes."
    source = "LoRA reduces trainable parameters substantially compared with full fine-tuning."
    assert assess_claim_span_grounding(claim, source, kind="recommendation") is None


def test_verify_marks_ungrounded_prose_unsupported():
    from app.domain.verify_citations import verify_against_sources
    from app.domain.schema import Claim

    claim = Claim(
        id="c2",
        text=(
            "LoRA-PAR dynamically allocates adapters based on layer-wise gradient sensitivity "
            "across every transformer block."
        ),
        quote="",
        url="https://example.org/lora-par",
        tier="peer_reviewed",
        kind="direct",
    )
    evidence = [{
        "url": "https://example.org/lora-par",
        "title": "LoRA-PAR",
        "tier": "peer_reviewed",
        "text": (
            "LoRA-PAR partitions tasks using a System 1 / System 2 scheme with importance scoring. "
            "No gradient-based allocation is used."
        ),
    }]
    out = verify_against_sources([claim], evidence, refetch=False)
    assert out["claims"][0]["verification_status"] == "unsupported"


def test_primary_source_audit_lora():
    from app.domain.report_audit import audit_memo_primary_sources

    notes = audit_memo_primary_sources(
        "LoRA LoRA LoRA QLoRA QLoRA fine-tuning details without primary citations.",
        query="lora vs qlora",
    )
    assert notes and "2106.09685" in notes[0]
    ok = audit_memo_primary_sources(
        "LoRA (Hu et al., https://arxiv.org/abs/2106.09685) and QLoRA "
        "(Dettmers et al., https://arxiv.org/abs/2305.14314) LoRA QLoRA LoRA QLoRA.",
        query="lora vs qlora",
    )
    assert ok == []
