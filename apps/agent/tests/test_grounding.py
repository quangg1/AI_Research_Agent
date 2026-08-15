from app.domain.grounding import verify_claim, verify_claims
from app.domain.schema import Budget
from app.graph.nodes.critic import critic_node
from app.retrieval.chunk import load_corpus
from app.retrieval.hybrid import hybrid_retrieve


def test_ungrounded_claim_is_flagged():
    evidence = [{
        "id": "ev1",
        "snippet": "Continuous batching lets new requests join a running batch.",
        "quote": "continuous batching",
        "url": "https://docs.vllm.ai",
        "tier": "standard_body",
        "credibility": 0.88,
    }]
    claim = {
        "id": "c1",
        "text": "RAG always requires a vector database",
        "support_ids": ["ev1"],
        "confidence": 0.9,
    }
    out = verify_claim(claim, evidence)
    assert out["confidence"] <= 0.35
    assert out["grounded"] is False


def test_grounded_claim_keeps_quote_and_url():
    evidence = [{
        "id": "ev1",
        "snippet": "Lexical BM25 and hybrid retrieval frequently match or beat dense-only RAG.",
        "quote": "hybrid retrieval frequently match or beat dense-only RAG",
        "url": "https://arxiv.org",
        "tier": "peer_reviewed",
        "credibility": 0.84,
    }]
    claim = {
        "id": "c1",
        "text": "Hybrid retrieval can match dense-only RAG.",
        "quote": "hybrid retrieval frequently match or beat dense-only RAG",
        "support_ids": ["ev1"],
        "confidence": 0.7,
    }
    out = verify_claim(claim, evidence)
    assert out["grounded"] is True
    assert out["url"].startswith("https://")


def test_critic_contradiction_emits_followups_when_budget_remains():
    docs = load_corpus()
    ranked = hybrid_retrieve("vector database BM25 hybrid retrieval LLM-as-judge bias", docs, k=8)
    state = {
        "query": "Compare a vector-only RAG stack vs BM25 plus a cross-encoder reranker.",
        "retrieved": ranked,
        "budget": Budget(max_iterations=3, iterations=1, max_tool_calls=12, used_tool_calls=3).model_dump(),
        "followups": [],
    }
    out = critic_node(state)
    assert out["critic"]["status"] in {"contradicted", "insufficient", "sufficient"}
    if out["critic"]["status"] != "sufficient":
        assert out["followups"]


def test_vector_myth_report_does_not_endorse_vector_only():
    from app.graph.nodes.report import _template_report

    docs = load_corpus()
    ranked = hybrid_retrieve("RAG vector database BM25 hybrid", docs, k=8)
    report = _template_report(
        {
            "query": "Does RAG always require a vector database?",
            "query_type": "factual",
            "budget": {"iterations": 1},
            "llm_mode": "heuristic",
        },
        ranked,
        {"status": "sufficient", "reasons": []},
    )
    blob = " ".join(c.text.lower() for c in report.claims)
    assert "always requires a vector" not in blob or "does not always" in blob
    verified = verify_claims(report.claims, ranked)
    assert verified
