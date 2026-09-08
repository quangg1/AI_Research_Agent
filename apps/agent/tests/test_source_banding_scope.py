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
