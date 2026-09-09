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


class _FakeEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeEmbedResult:
    def __init__(self, embeddings):
        self.embeddings = embeddings


def test_gemini_embed_sends_one_batch_call_not_one_per_text(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "google_api_key", "AIzaKeyOne12345678")
    calls: list[list[str]] = []

    class FakeModels:
        def embed_content(self, *, model, contents):
            calls.append(list(contents))
            return _FakeEmbedResult([_FakeEmbedding([1.0, 0.0]) for _ in contents])

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr("google.genai.Client", FakeClient)
    vectors = embed._gemini_embed(["a", "b", "c"])
    assert calls == [["a", "b", "c"]]
    assert len(vectors) == 3


def test_gemini_embed_rotates_to_next_key_on_failure(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "google_api_key", "AIzaKeyOne12345678;AIzaKeyTwo12345678")
    tried_keys: list[str] = []

    class FakeModels:
        def __init__(self, key):
            self.key = key

        def embed_content(self, *, model, contents):
            tried_keys.append(self.key)
            if self.key.endswith("One12345678"):
                raise RuntimeError("429 rate limited")
            return _FakeEmbedResult([_FakeEmbedding([1.0, 0.0]) for _ in contents])

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels(api_key)

    monkeypatch.setattr("google.genai.Client", FakeClient)
    vectors = embed._gemini_embed(["a", "b"])
    assert tried_keys == ["AIzaKeyOne12345678", "AIzaKeyTwo12345678"]
    assert len(vectors) == 2


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
    # Each page is prefixed with a [[page N]] locator marker for citation spans.
    assert text.startswith("[[page 1]]")
    assert "evidence" in text
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


class _FakeHttpxResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpxClient:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, *_args, **_kwargs):
        return _FakeHttpxResponse(self._payload)


def test_openalex_prefers_open_access_copy_over_paywalled_doi(monkeypatch):
    """Regression: primary_location is often the paywalled publisher page.
    OpenAlex separately reports a free copy in best_oa_location — the old
    code ignored it and always cited the DOI redirect, so enrich could never
    read the paper (real run: 0/6 doi.org sources got full_text)."""
    q = "synthetic data agentic pipeline benchmark evaluation"
    payload = {
        "results": [
            {
                "display_name": "Synthetic Data Agentic Pipeline Benchmark Evaluation",
                "abstract_inverted_index": {"synthetic": [0], "data": [1], "agentic": [2], "benchmark": [3]},
                "doi": "https://doi.org/10.1000/paywalled",
                "primary_location": {
                    "landing_page_url": "https://doi.org/10.1000/paywalled",
                    "source": {"type": "journal", "display_name": "Some Journal"},
                },
                "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2401.00001"},
                "publication_year": 2024,
                "id": "https://openalex.org/W1",
            }
        ]
    }
    monkeypatch.setattr(scholar.httpx, "Client", lambda timeout=20: _FakeHttpxClient(payload))
    rows = scholar._openalex(q)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://arxiv.org/pdf/2401.00001"
    # The journal DOI + venue metadata say peer-reviewed — must not be
    # demoted to specialist_research just because the free copy is on arXiv.
    assert rows[0]["tier"] == "peer_reviewed"


def test_semantic_scholar_prefers_arxiv_and_oa_pdf_over_doi(monkeypatch):
    q = "synthetic data agentic pipeline benchmark evaluation"
    payload = {
        "data": [
            {
                "title": "Synthetic Data Agentic Pipeline Benchmark Evaluation",
                "abstract": "synthetic data agentic benchmark",
                "externalIds": {"DOI": "10.1000/paywalled", "ArXiv": "2401.00001"},
                "publicationTypes": [],
                "venue": "",
                "year": 2024,
            }
        ]
    }
    monkeypatch.setattr(scholar.httpx, "Client", lambda timeout=15: _FakeHttpxClient(payload))
    rows = scholar._semantic_scholar(q)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://arxiv.org/abs/2401.00001"
    assert rows[0]["tier"] == "peer_reviewed"


def test_retrieve_node_keeps_covered_dimension_evidence_across_iterations(monkeypatch):
    """Real run: once the critic marks a dimension "covered", retrieve_node
    skips re-retrieving it (to save budget) — but used to then REPLACE
    `retrieved` wholesale with only the still-open dimensions' hits, dropping
    the covered dimension's evidence entirely. The next coverage scoring pass
    then saw evidence that no longer contained it and flipped the slot back
    to "open", so depth/coverage oscillated iteration to iteration instead of
    climbing (measured live: depth 68/55/78/58/78/56 across 6 iterations of
    one run). Evidence already found for a covered dimension must be carried
    forward, not dropped."""
    from app.graph.nodes import collector
    from app.graph.nodes.collector import retrieve_node

    monkeypatch.setattr(collector, "search_qdrant", lambda *a, **kw: [])

    covered_evidence = {
        "id": "latency-src",
        "url": "https://arxiv.org/abs/2401.00001",
        "title": "Multi-Agent Latency Study",
        "snippet": "Multi-agent systems add coordination latency overhead compared to a single agent.",
        "quote": "Multi-agent systems add coordination latency overhead compared to a single agent.",
    }
    open_evidence = {
        "id": "framework-src",
        "url": "https://arxiv.org/abs/2401.00002",
        "title": "LangGraph AutoGen CrewAI Framework Comparison",
        "snippet": "LangGraph, AutoGen, and CrewAI differ in their multi-agent orchestration model.",
        "quote": "LangGraph, AutoGen, and CrewAI differ in their multi-agent orchestration model.",
    }
    state = {
        "query": "single-agent vs multi-agent LLM architectures orchestration latency",
        "evidence": [covered_evidence, open_evidence],
        "retrieved": [covered_evidence],
        "critic": {
            "coverage": {
                "slots": [
                    {
                        "id": "latency",
                        "label": "Latency",
                        "status": "covered",
                        "patterns": [],
                        "topic_terms": ["latency"],
                    },
                    {
                        "id": "frameworks",
                        "label": "LangGraph AutoGen CrewAI",
                        "status": "open",
                        "patterns": [],
                        "topic_terms": ["langgraph", "autogen", "crewai"],
                    },
                ]
            }
        },
        "budget": {},
    }
    out = retrieve_node(state)
    retrieved_ids = {e.get("id") for e in out["retrieved"]}
    assert "latency-src" in retrieved_ids, "covered dimension's evidence must not be dropped"
    assert "framework-src" in retrieved_ids
