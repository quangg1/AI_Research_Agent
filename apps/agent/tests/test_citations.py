from app.domain.citations import bind_markdown_to_ledger, build_ledger, is_citable_url, pick_quote
from app.domain.schema import SourceTier
from app.graph.nodes.scholar import _on_topic, _openalex_query
from app.report.compose import compose_report
from app.retrieval.chunk import load_corpus


def test_rejects_site_homepages():
    assert not is_citable_url("https://huggingface.co")
    assert not is_citable_url("https://huggingface.co/")
    assert not is_citable_url("https://arxiv.org")
    assert not is_citable_url("https://arxiv.org/")
    assert not is_citable_url("https://python.langchain.com")
    assert is_citable_url("https://huggingface.co/blog/how-to-train-sentence-transformers")
    assert is_citable_url("https://arxiv.org/abs/2005.11401")
    assert is_citable_url("https://doi.org/10.48550/arXiv.2005.11401")


def test_ledger_skips_homepages():
    evidence = [
        {"id": "bad", "title": "Hub", "url": "https://huggingface.co", "snippet": "chunking rerank", "quote": "chunking rerank", "credibility": 0.99, "tier": "official_regulation"},
        {"id": "good", "title": "RAG paper", "url": "https://arxiv.org/abs/2005.11401", "snippet": "retrieval augmented generation", "quote": "retrieval augmented generation", "credibility": 0.8, "tier": "peer_reviewed"},
    ]
    ledger = build_ledger(evidence, k=5)
    assert [c.evidence_id for c in ledger] == ["good"]
    assert all(is_citable_url(c.url) for c in ledger)


def test_bind_replaces_invented_bibliography():
    citations = [
        {"n": 1, "evidence_id": "good", "title": "RAG paper", "url": "https://arxiv.org/abs/2005.11401", "quote": "x", "tier": "peer_reviewed", "host": "arxiv.org"}
    ]
    md = (
        "## Findings\nSee [Healthcare RAG](https://huggingface.co/) and [fake](https://arxiv.org/abs/2502.11371).\n"
        "## Core references\n1. Invented healthcare survey\n"
    )
    out = bind_markdown_to_ledger(md, citations)
    assert "huggingface.co/" not in out
    assert "2502.11371" not in out
    assert "https://arxiv.org/abs/2005.11401" in out
    assert "Healthcare RAG" in out  # link dropped, title kept


def test_bind_keeps_exactly_one_reference_section():
    citations = [
        {"n": 1, "evidence_id": "p", "title": "Punica", "url": "https://arxiv.org/abs/2306.06678", "quote": "SGMV", "tier": "peer_reviewed", "host": "arxiv.org"}
    ]
    md = (
        "# Memo\n\n## Findings\nMechanism [1].\n\n"
        "## References\n\n1. Existing\n\n"
        "## Core references\n\n1. Duplicate\n"
    )
    out = bind_markdown_to_ledger(md, citations)
    assert out.count("## References") == 1
    assert "## Core references" not in out
    assert out.count("https://arxiv.org/abs/2306.06678") == 2  # markdown target + rendered URL


def test_bind_drops_ranked_but_never_cited_sources():
    """Regression: build_ledger ranks up to k=12 candidate sources, but the
    writer doesn't necessarily cite all of them — a real memo had 4/12
    references never appear as [n] anywhere in the body, padding the list
    with sources the memo never actually draws on."""
    citations = [
        {"n": 1, "evidence_id": "a", "title": "Used paper", "url": "https://arxiv.org/abs/2401.00001", "quote": "x", "tier": "peer_reviewed", "host": "arxiv.org"},
        {"n": 2, "evidence_id": "b", "title": "Never cited paper", "url": "https://arxiv.org/abs/2401.00002", "quote": "x", "tier": "peer_reviewed", "host": "arxiv.org"},
    ]
    md = "## Findings\nCore claim here [1].\n"
    out = bind_markdown_to_ledger(md, citations)
    assert "2401.00001" in out
    assert "2401.00002" not in out


def test_healthcare_scholar_dropped_unless_asked():
    title = "A survey on retrieval-augmentation generation (RAG) models for healthcare applications"
    assert not _on_topic(title, "", "Compare BM25 plus a cross-encoder reranker")
    assert _on_topic(title, "", "RAG for clinical notes in a hospital")


def test_openalex_query_strips_slashes():
    q = "How does Punica/LoRAX batch multiple LoRA adapters in one forward pass?"
    assert "/" not in _openalex_query(q)
    assert "Punica" in _openalex_query(q)


def test_scholar_keeps_punica_paper_not_random_rag_survey():
    q = "How does Punica/LoRAX batch multiple LoRA adapters in one forward pass?"
    assert _on_topic("Punica: Multi-Tenant LoRA Serving", "batch multiple LoRA adapters in one forward pass", q)
    assert not _on_topic("A survey on RAG models for healthcare applications", "retrieval augmentation generation", q)


def test_corpus_notes_are_not_vendor_primary_docs():
    docs = load_corpus()
    assert docs
    assert all(d.get("source_kind") == "corpus_note" for d in docs)
    assert all(d.get("tier") == SourceTier.SPECIALIST_RESEARCH.value for d in docs)
    assert all(float(d.get("credibility") or 0) <= 0.68 for d in docs)
    sample = next(d for d in docs if "Fine-tune vs RAG" in (d.get("title") or ""))
    assert not (sample.get("snippet") or "").startswith("# ")
    assert "Source:" not in (sample.get("snippet") or "")


def test_pick_quote_skips_front_matter_and_chrome():
    q = pick_quote({
        "quote": "# Fine-tune vs RAG\nSource: Applied LLM systems notes.\nFine-tuning is a poor default for facts that change weekly.",
    })
    assert "Source:" not in q
    assert not q.startswith("#")
    assert "poor default" in q.lower()
    chrome = pick_quote({
        "quote": "Train Models Hugging Face Models Datasets Spaces Buckets new Docs Enterprise Pricing",
        "title": "Sentence transformers",
    })
    assert "Datasets Spaces" not in chrome


def test_ledger_dedupes_same_url():
    evidence = [
        {"id": "a", "title": "One", "url": "https://arxiv.org/abs/2005.11401", "snippet": "retrieval augmented generation for knowledge", "quote": "retrieval augmented generation for knowledge", "credibility": 0.9, "tier": "peer_reviewed"},
        {"id": "b", "title": "Two", "url": "https://arxiv.org/abs/2005.11401", "snippet": "RAG retrieves then generates", "quote": "RAG retrieves then generates", "credibility": 0.8, "tier": "peer_reviewed"},
    ]
    ledger = build_ledger(evidence, k=5)
    assert len(ledger) == 1


def test_learning_memo_does_not_use_rag_architecture_rule():
    docs = load_corpus()
    report = compose_report(
        query="Design a high-yield learning path for mastering LLM systems and RAG",
        evidence=docs[:6],
        critic={"status": "sufficient"},
        plan={"query_type": "open_research", "agents_to_run": ["search", "scholar"]},
    )
    assert "learning-path" in report.executive_summary.lower() or "learning path" in report.decision_rule.lower() or "Learn by shipping" in report.decision_rule
    assert "Add a vector database when paraphrase recall" not in report.decision_rule


def test_corpus_urls_are_landing_pages():
    docs = load_corpus()
    urls = {d.get("url") for d in docs if d.get("url")}
    assert urls
    assert all(is_citable_url(u) for u in urls)
    assert "https://huggingface.co" not in urls
    assert "https://arxiv.org" not in urls
