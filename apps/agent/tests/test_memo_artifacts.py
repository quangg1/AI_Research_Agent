from app.domain.research_intent import is_methodology_eval_query
from app.report.memo_artifacts import (
    build_thesis_paragraph,
    evidence_matrix_markdown,
    find_contrast_pairs,
    inject_structured_sections,
)


SYNTH_Q = (
    "Analyze methodologies, evaluation frameworks, and failure modes associated with using "
    "synthetic data and autonomous data-generation agents to address high-quality data scarcity."
)


def test_methodology_query_detection():
    assert is_methodology_eval_query(SYNTH_Q)
    assert not is_methodology_eval_query("How does Punica batch LoRA adapters?")


def test_evidence_matrix_and_contrast_pairs():
    evidence = [
        {
            "title": "German law synthetic QA",
            "url": "https://arxiv.org/html/2508.18929v1",
            "snippet": "Difficulty-graded filtering improved LegalMC4 QA from 43.0% to 55.4%.",
            "tier": "peer_reviewed",
        },
        {
            "title": "German law simple QA",
            "url": "https://arxiv.org/html/2508.18930v1",
            "snippet": "Simple instruction QA decreased LegalMC4 QA from 43.0% to 35.4%.",
            "tier": "peer_reviewed",
        },
    ]
    citations = [{"n": 1, "url": evidence[0]["url"]}, {"n": 2, "url": evidence[1]["url"]}]
    legal_q = "Analyze synthetic legal QA generation and evaluation for regulatory compliance."
    md = evidence_matrix_markdown(evidence, citations, query=legal_q)
    assert "## Evidence matrix" in md
    assert "43.0%" in md or "55.4%" in md
    pairs = find_contrast_pairs(legal_q, evidence, citations)
    assert pairs
    assert "positive" in pairs[0]


def test_thesis_paragraph_is_conditional():
    from app.domain.citations import Citation

    ledger = [
        Citation(
            n=1,
            evidence_id="a",
            title="Synthetic survey",
            url="https://arxiv.org/abs/1",
            quote="synthetic data scarcity in specialized domains",
            tier="peer_reviewed",
        )
    ]
    thesis = build_thesis_paragraph(
        SYNTH_Q,
        [{"title": "Synthetic data", "url": "https://arxiv.org/abs/1", "snippet": "synthetic data scarcity"}],
        ledger,
        [{"id": "m", "label": "Methods", "status": "weak", "items": []}],
    )
    assert "synthetic" in thesis.lower()
    assert "only when" in thesis.lower() or "conditional" in thesis.lower()


def test_inject_structured_sections_adds_missing_headings():
    body = "# Title\n\n## Executive summary\n\nAnswer.\n\n## Key findings\n\n1. One.\n\n## Detailed analysis\n\n### X\n\nY.\n"
    evidence = [
        {
            "title": "Self-Instruct",
            "url": "https://arxiv.org/abs/2212.10560",
            "snippet": "Self-Instruct improved Super-NaturalInstructions by 33 absolute points.",
            "tier": "peer_reviewed",
        }
    ]
    citations = [{"n": 1, "url": evidence[0]["url"]}]
    out = inject_structured_sections(body, SYNTH_Q, evidence, citations, [], {})
    assert "## Evidence matrix" in out
    assert "## Evaluation chain" in out
