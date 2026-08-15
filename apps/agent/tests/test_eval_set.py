from app.eval.runner import load_cases, score_case, score_report_quality
from app.retrieval.chunk import load_corpus
from app.retrieval.hybrid import hybrid_retrieve


def test_golden_routing_majority():
    rows = [score_case(c) for c in load_cases()]
    passed = sum(1 for r in rows if r["pass"])
    assert passed == len(rows), rows


def test_eval_scores_required_tiers_and_expected_contradiction():
    missing_scholar = score_case(
        {
            "id": "tier-check",
            "query": "Can retrieved web pages instruct the agent to call privileged tools?",
            "expected_type": "factual",
            "required_tiers": ["peer_reviewed"],
        }
    )
    missing_contradiction = score_case(
        {
            "id": "contradiction-check",
            "query": "What traces does a LangGraph research agent emit for cost attribution?",
            "expected_type": "factual",
            "expect_contradiction": True,
        }
    )

    assert not missing_scholar["tiers_ok"]
    assert not missing_scholar["pass"]
    assert not missing_contradiction["contradiction_ok"]
    assert not missing_contradiction["pass"]


def test_report_citation_accuracy_on_corpus():
    docs = load_corpus()
    ranked = hybrid_retrieve("Does RAG always require a vector database?", docs, k=8)
    score = score_report_quality("Does RAG always require a vector database?", ranked)
    assert score["has_memo"]
    assert score["citations"] >= 3
    assert score["citation_accuracy"] >= 0.5
