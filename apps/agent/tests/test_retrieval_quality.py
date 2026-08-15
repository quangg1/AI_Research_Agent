from concurrent.futures import ThreadPoolExecutor

import app.retrieval.embed as embed
import app.retrieval.hybrid as hybrid
import app.tools.fetch as fetch
import app.graph.nodes.scholar as scholar
from app.domain.decompose import rewrite_gap_query
from app.domain.schema import AgentName
from app.graph.nodes.scholar import _dedupe_papers, _publication_type


def test_deterministic_reranker_rewards_direct_evidence():
    rows = [
        {
            "id": "background",
            "title": "Vector systems overview",
            "snippet": "A broad overview of vector systems.",
            "retrieval_score": 0.9,
        },
        {
            "id": "direct",
            "title": "BM25 reranker latency benchmark",
            "snippet": "We measured BM25 reranker latency and results show a 20 percent reduction.",
            "retrieval_score": 0.7,
        },
    ]
    ranked = hybrid.rerank_candidates("BM25 reranker latency benchmark", rows, k=2)
    assert ranked[0]["id"] == "direct"
    assert ranked[0]["rerank_method"] == "deterministic"
    assert ranked[0]["rerank_external_calls"] == 0


def test_reranker_falls_back_when_model_scoring_fails(monkeypatch):
    monkeypatch.setattr(hybrid, "_gemini_rerank", lambda *_args, **_kwargs: {})
    rows = [{"id": "a", "title": "Exact target", "snippet": "Exact target details", "retrieval_score": 0.8}]
    ranked = hybrid.rerank_candidates("Exact target", rows, use_llm=True)
    assert ranked[0]["rerank_method"] == "deterministic"


def test_embedding_cache_reuses_unchanged_documents(monkeypatch):
    embed.clear_embedding_cache()
    calls = []

    def fake_uncached(texts):
        calls.append(list(texts))
        return [[float(len(text))] for text in texts]

    monkeypatch.setattr(embed, "_embed_uncached", fake_uncached)
    first = embed.embed_texts(["alpha corpus", "beta corpus"])
    second = embed.embed_texts(["alpha corpus", "beta corpus"])
    assert first == second
    assert calls == [["alpha corpus", "beta corpus"]]
    assert embed.embedding_cache_stats()["hits"] == 2


def test_embedding_cache_is_thread_safe_and_bounded(monkeypatch):
    embed.clear_embedding_cache()
    monkeypatch.setattr(embed, "_CACHE_MAX", 3)
    monkeypatch.setattr(embed, "_embed_uncached", lambda texts: [[float(len(text))] for text in texts])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: embed.embed_texts([f"document {i % 5}"]), range(30)))
    assert embed.embedding_cache_stats()["size"] <= 3


def test_html_extraction_prefers_article_and_drops_chrome():
    raw = b"""
    <html><body><nav>Pricing Login</nav><article><h1>Finding</h1>
    <p>The full result explains the mechanism.</p><script>ignore()</script></article>
    <footer>Cookies</footer></body></html>
    """
    text = fetch.extract_content(raw, "text/html; charset=utf-8")
    assert "Finding" in text and "full result" in text
    assert "Pricing" not in text and "ignore" not in text and "Cookies" not in text


def test_pdf_extraction_has_page_and_character_limits(monkeypatch):
    class Page:
        def extract_text(self):
            return "evidence " * 10_000

    class Reader:
        def __init__(self, _stream):
            self.pages = [Page()] * (fetch.MAX_PDF_PAGES + 10)

    monkeypatch.setattr(fetch, "PdfReader", Reader)
    text = fetch.extract_content(b"%PDF-fake", "application/pdf")
    assert text.startswith("evidence")
    assert len(text) <= fetch.MAX_EXTRACTED_CHARS


def test_gap_rewrite_preserves_targets_and_routes_empirical_gap():
    query = "How do Punica and LoRAX batch LoRA adapters without increasing GPU memory?"
    gap = {"id": "quantitative", "label": "Measured benchmark results", "status": "open"}
    rewritten, agent = rewrite_gap_query(query, gap)
    assert "Punica" in rewritten and "LoRAX" in rewritten
    assert "Measured benchmark results" in rewritten
    assert agent == AgentName.SCHOLAR


def test_scholar_dedupes_doi_and_labels_preprints():
    rows = [
        {"id": "a", "doi": "10.1234/example", "url": "https://doi.org/10.1234/example"},
        {"id": "b", "doi": "10.1234/EXAMPLE", "url": "https://example.org/paper"},
        {"id": "c", "doi": "", "url": "https://arxiv.org/abs/2401.00001"},
    ]
    assert len(_dedupe_papers(rows)) == 2
    assert _publication_type("https://arxiv.org/abs/2401.00001", "", []) == "preprint"
    assert _publication_type("https://doi.org/10.1234/x", "10.1234/x", []) == "peer_reviewed"


def test_semantic_scholar_augments_thin_openalex_results(monkeypatch):
    openalex = [{"id": "oa", "doi": "10.1/shared", "url": "https://doi.org/10.1/shared"}]
    semantic = [
        {"id": "s1", "doi": "10.1/shared", "url": "https://example.org/duplicate"},
        {"id": "s2", "doi": "10.1/new", "url": "https://doi.org/10.1/new"},
    ]
    monkeypatch.setattr(scholar, "_openalex", lambda _query: openalex)
    monkeypatch.setattr(scholar, "_semantic_scholar", lambda _query: semantic)
    rows, calls = scholar._scholar_search("target")
    assert [row["id"] for row in rows] == ["oa", "s2"]
    assert calls == 2
