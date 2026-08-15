from app.domain.citations import build_ledger, quote_in_source
from app.report.compose import compose_report, compose_terminal_synthesis, memo_is_user_clean
from app.retrieval.chunk import load_corpus
from app.retrieval.hybrid import hybrid_retrieve


def test_quote_in_source_substring():
    src = "Lexical BM25 and hybrid retrieval frequently match or beat dense-only RAG."
    assert quote_in_source("hybrid retrieval frequently match or beat dense-only RAG", src)
    assert not quote_in_source("quantum teleportation of embeddings", src)


def test_ledger_assigns_monotonic_n():
    docs = load_corpus()
    ranked = hybrid_retrieve("RAG hybrid BM25 vector database", docs, k=6)
    ledger = build_ledger(ranked, k=5)
    assert ledger
    assert [c.n for c in ledger] == list(range(1, len(ledger) + 1))
    assert all(c.quote for c in ledger)


def test_compose_has_user_facing_sections():
    docs = load_corpus()
    ranked = hybrid_retrieve("Does RAG always require a vector database?", docs, k=8)
    report = compose_report(
        query="Does RAG always require a vector database?",
        evidence=ranked,
        critic={"status": "contradicted", "reasons": ["BM25 vs dense-only."]},
        plan={"query_type": "factual", "agents_to_run": ["docs", "search"]},
        llm_mode="heuristic",
    )
    body = report.body_markdown
    for heading in ("Executive summary", "Scope", "Findings", "Decision rule", "Limitations", "References"):
        assert heading in body
    assert "[" in body
    assert report.citations
    assert memo_is_user_clean(body)
    assert report.metrics.get("diagnostics_markdown")
    assert "Claim ledger" in report.metrics["diagnostics_markdown"]


def test_user_memo_excludes_internal_telemetry():
    docs = load_corpus()
    ranked = hybrid_retrieve("Punica SGMV multi-LoRA batching", docs, k=8)
    report = compose_report(
        query="How does Punica SGMV batch multiple LoRA adapters?",
        evidence=ranked,
        critic={
            "status": "insufficient",
            "coverage": {"slots": [{"id": "kernel_mechanism", "label": "SGMV", "status": "weak", "critical": True}]},
        },
        plan={"query_type": "open_research", "agents_to_run": ["search", "scholar"]},
        brief={"depth": "deep"},
        budget={"iterations": 3, "max_iterations": 3, "used_tool_calls": 10, "max_tool_calls": 12},
        llm_mode="heuristic",
    )
    body = report.body_markdown.lower()
    assert "tool calls" not in body
    assert "claim ledger" not in body
    assert "working set" not in body
    assert "critic status" not in body
    assert memo_is_user_clean(report.body_markdown)


def test_terminal_synthesis_marks_status_and_keeps_complete_memo():
    docs = load_corpus()
    ranked = hybrid_retrieve("LoRAX prefetch offloading adapters", docs, k=6)
    followups = [{"question": "site:github.com predibase lorax memory layout", "agent": "search"}]
    report = compose_terminal_synthesis(
        query="How does LoRAX manage adapter memory and prefetching?",
        evidence=ranked,
        critic={"status": "insufficient", "coverage": {"critical_gaps": [{"id": "memory_layout", "label": "Memory layout"}]}},
        brief={"depth": "deep"},
        budget={"iterations": 3, "max_iterations": 3},
        terminal_followups=followups,
    )
    assert report.metrics.get("synthesis_status") == "terminal_fallback"
    assert "## Executive summary" in report.body_markdown
    assert "## References" in report.body_markdown
    assert any("memory" in x.lower() or "follow-up" in x.lower() or "research" in x.lower() for x in report.limitations)


def test_analysis_section_is_built_from_the_question_dimensions():
    query = "How does CRISPR-Cas9 cut a target DNA sequence inside a living cell?"
    evidence = [
        {
            "id": "e1",
            "title": "Cas9 mechanism review",
            "url": "https://www.nature.com/articles/nmeth.1234",
            "snippet": (
                "Cas9 works by pairing the guide RNA with a complementary DNA target, then the HNH domain "
                "cleaves one strand while RuvC cleaves the other in a stepwise process."
            ),
            "quote": (
                "Cas9 works by pairing the guide RNA with a complementary DNA target, then the HNH domain "
                "cleaves one strand while RuvC cleaves the other in a stepwise process."
            ),
            "tier": "peer_reviewed",
            "credibility": 0.9,
        }
    ]
    slots = [
        {
            "id": "mechanism",
            "label": "How the cut is made, step by step",
            "status": "covered",
            "critical": True,
            "evidence_ids": ["e1"],
            "patterns": ["works by", "process", "step"],
            "topic_terms": ["cas9", "dna", "guide"],
        }
    ]
    report = compose_report(
        query=query,
        evidence=evidence,
        critic={"status": "sufficient", "coverage": {"slots": slots}},
        brief={"depth": "deep", "must_answer": slots},
    )
    body = report.body_markdown
    assert "## Analysis" in body
    assert "How the cut is made, step by step" in body
    assert "HNH domain" in body
    assert "SGMV" not in body and "LoRA" not in body


def test_comparison_cells_are_scoped_to_each_subject():
    query = (
        "Analyze how Punica, S-LoRA, vLLM, and TGI serve concurrent requests "
        "using different LoRA adapters on one shared base model"
    )
    evidence = [
        {
            "id": "punica",
            "title": "Punica: Multi-Tenant LoRA Serving",
            "url": "https://arxiv.org/abs/2306.06678",
            "snippet": "Punica uses SGMV segmented gather kernels so concurrent adapters share one base pass.",
            "quote": "Punica uses SGMV segmented gather kernels so concurrent adapters share one base pass.",
            "credibility": 0.9,
            "tier": "peer_reviewed",
        },
        {
            "id": "slora",
            "title": "S-LoRA: Serving Thousands of Concurrent LoRA Adapters",
            "url": "https://arxiv.org/abs/2311.03285",
            "snippet": "S-LoRA introduces unified paging for adapter weights and the KV cache on shared hardware.",
            "quote": "S-LoRA introduces unified paging for adapter weights and the KV cache on shared hardware.",
            "credibility": 0.9,
            "tier": "peer_reviewed",
        },
        {
            "id": "vllm",
            "title": "vLLM PagedAttention and Multi-LoRA",
            "url": "https://docs.vllm.ai/en/latest/features/lora.html",
            "snippet": "vLLM serves multiple concurrent LoRA adapters and pages the KV cache with PagedAttention.",
            "quote": "vLLM serves multiple concurrent LoRA adapters and pages the KV cache with PagedAttention.",
            "credibility": 0.9,
            "tier": "standard_body",
        },
    ]
    slots = [
        {
            "id": "mechanism",
            "label": "How concurrent adapters share one model",
            "status": "covered",
            "critical": True,
            "evidence_ids": ["punica", "slora", "vllm"],
        }
    ]
    report = compose_report(
        query=query,
        evidence=evidence,
        critic={"status": "sufficient", "coverage": {"slots": slots}},
        brief={"depth": "deep", "must_answer": slots},
    )
    body = report.body_markdown
    assert "## Comparison" in body
    row = next(line for line in body.splitlines() if line.startswith("| How concurrent adapters"))
    cells = [c.strip() for c in row.strip("|").split("|")[1:]]
    filled = [c for c in cells if c and "Not established" not in c]
    assert len(filled) >= 2
    assert len(set(filled)) == len(filled)  # no passage copied across subjects
    assert "## Core references" not in body


def test_long_report_title_is_not_truncated():
    query = (
        "Analyze how modern LLM inference systems including S-LoRA Punica vLLM and TGI "
        "efficiently serve concurrent requests using different LoRA adapters on a shared base model"
    )
    report = compose_report(query=query, evidence=[], brief={"goal": query})
    assert report.title == query
    assert "…" not in report.title
