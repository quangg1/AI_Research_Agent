from app.retrieval.chunk import load_corpus
from app.retrieval.hybrid import corpus_is_relevant, hybrid_retrieve


def test_corpus_loads():
    docs = load_corpus()
    assert len(docs) >= 8


def test_hybrid_ranks_rag_notes_ahead_of_unrelated():
    docs = load_corpus()
    ranked = hybrid_retrieve("RAG hybrid BM25 vector database reranker", docs, k=5)
    assert ranked
    blob = " ".join(d["snippet"].lower() for d in ranked[:3])
    assert "bm25" in blob or "vector" in blob or "rag" in blob
    score = ranked[0]["retrieval_score"]
    assert type(score) is float


def test_off_corpus_query_does_not_dump_internal_notes():
    docs = load_corpus()
    q = "How does Punica/LoRAX batch multiple LoRA adapters in one forward pass?"
    assert not corpus_is_relevant(q, docs)
    ranked = hybrid_retrieve(q, docs, k=8)
    titles = " ".join((d.get("title") or "") for d in ranked).lower()
    assert ranked == []
    assert "rag" not in titles
    assert "hybrid" not in titles


def test_in_corpus_vllm_query_still_hits_notes():
    docs = load_corpus()
    q = "What is continuous batching in vLLM and how does it affect time-to-first-token?"
    assert corpus_is_relevant(q, docs)
    ranked = hybrid_retrieve(q, docs, k=5)
    assert ranked
    blob = " ".join(f"{d.get('title','')} {d.get('snippet','')}".lower() for d in ranked[:3])
    assert "vllm" in blob or "batch" in blob or "paged" in blob
