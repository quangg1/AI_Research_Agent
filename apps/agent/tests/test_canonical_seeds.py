from app.domain.canonical_seeds import merge_seed_evidence, methodology_scholar_queries
from app.domain.citations import build_ledger
from app.domain.research_intent import flip_condition_for
from app.report.memo_artifacts import find_contrast_pairs

SYNTH_Q = (
    "Analyze synthetic data and data-generation agents for specialized domain scarcity, "
    "model collapse, and evaluation frameworks."
)


def test_merge_seed_evidence_adds_classics():
    merged = merge_seed_evidence(SYNTH_Q, [])
    urls = {(e.get("url") or "").lower() for e in merged}
    assert "https://arxiv.org/abs/2212.10560" in urls
    assert "https://www.nature.com/articles/s41586-024-07566-y" in urls
    assert len(methodology_scholar_queries(SYNTH_Q, limit=8)) >= 5


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


def test_contrast_pairs_reject_invalid_cite():
    evidence = [
        {
            "title": "Gain study",
            "url": "https://arxiv.org/abs/1",
            "snippet": "Synthetic pipeline improved accuracy by 20% on LegalMC4 legal QA.",
        },
        {
            "title": "Harm study",
            "url": "https://arxiv.org/abs/2",
            "snippet": "Simple QA decreased LegalMC4 accuracy by 7.6% on legal QA tasks.",
        },
    ]
    citations = [{"n": 1, "url": evidence[0]["url"]}]
    pairs = find_contrast_pairs(
        "legal synthetic QA evaluation for regulatory compliance",
        evidence,
        citations,
    )
    assert not pairs
