from app.domain.citations import build_ledger
from app.domain.research_intent import flip_condition_for

SYNTH_Q = (
    "Analyze synthetic data and data-generation agents for specialized domain scarcity, "
    "model collapse, and evaluation frameworks."
)


def test_build_ledger_marks_arxiv_preprint():
    evidence = [
        {
            "id": "e1",
            "title": "APIGen paper",
            "url": "https://arxiv.org/abs/2504.03601",
            "snippet": "Multi-turn agentic data generation improves tool use.",
            "tier": "specialist_research",
            "publication_type": "preprint",
        }
    ]
    ledger = build_ledger(evidence, k=5, query=SYNTH_Q)
    assert ledger
    assert ledger[0].publication_status == "preprint"


def test_flip_condition_is_readable():
    text = flip_condition_for(SYNTH_Q, {})
    assert "citation measured" not in text.lower()
    assert len(text) > 40
