from app.graph.nodes.report import _format_dossier_for_prompt, _llm_report
from app.llm.client import bind_llm, reset_llm
from app.report.deep_write import (
    compress_prompt,
    compress_system,
    format_research_notes,
    parse_report_markdown,
    should_skip_llm_compress,
    word_target,
    writer_prompt,
    writer_system,
)


def _dimension(n: int) -> dict:
    return {
        "id": "mechanism",
        "label": "How the serving path batches adapters",
        "status": "covered",
        "items": [
            {
                "title": f"Source {i}",
                "url": f"https://docs.example.org/{i}",
                "quote": f"S-LoRA pages adapters independently {i}. " * 8,
                "tier": "standard_body",
            }
            for i in range(n)
        ],
    }


def test_word_targets_match_deep_research_length():
    assert word_target("quick") >= 900
    assert word_target("standard") >= 1800
    assert word_target("deep") >= 5000


def test_research_notes_keep_more_than_two_sources_per_dimension():
    citations = [{"n": i + 1, "url": f"https://docs.example.org/{i}"} for i in range(6)]
    notes = format_research_notes([_dimension(6)], citations)
    assert notes.count("- [") >= 6
    assert "Source 0" in notes and "Source 5" in notes
    assert "Quantitative fragments" in notes
    assert "Protected operational detail" in notes


def test_deep_notes_keep_twelve_sources_and_skip_llm_compress():
    citations = [{"n": i + 1, "url": f"https://docs.example.org/{i}"} for i in range(12)]
    notes = format_research_notes([_dimension(12)], citations, depth="deep")
    assert notes.count("- [") >= 12
    assert should_skip_llm_compress("deep") is True
    assert should_skip_llm_compress("standard") is False


def test_compress_prompt_asks_to_clean_not_deep_summarize():
    prompt = compress_prompt("- [1] PIVOT runs 19 failure modes.", "How do agents fail?", depth="standard")
    assert "do not deeply summarize" in prompt.lower() or "CLEAN" in prompt
    assert "Failure-mode inventory" in prompt
    assert "Operational mechanisms" in prompt
    assert "CLEAN" in compress_system() or "do not deeply summarize" in compress_system().lower()
    assert "19 failure modes" in compress_system()


def test_format_dossier_for_prompt_uses_dense_notes():
    citations = [{"n": 1, "url": "https://docs.example.org/0"}]
    text = _format_dossier_for_prompt([_dimension(3)], citations)
    assert "Source 2" in text
    assert "Protected operational detail" in text


def test_parse_report_markdown_keeps_full_body():
    memo = """# vLLM vs TGI for LoRA serving

## Executive summary

PagedAttention plus adapter paging is the load-bearing design. [1]

## Decision rule

Prefer vLLM when the workload is multi-LoRA and latency-bound. [1]

## Limitations

- No production trace for this cluster.

## References

[1] vLLM docs
"""
    parsed = parse_report_markdown(memo)
    assert parsed["title"] == "vLLM vs TGI for LoRA serving"
    assert "PagedAttention" in parsed["executive_summary"]
    assert "multi-LoRA" in parsed["decision_rule"]
    assert parsed["limitations"] == ["No production trace for this cluster."]
    assert parsed["body_markdown"].startswith("# vLLM")


def test_writer_prompt_asks_for_long_markdown_not_json():
    prompt = writer_prompt(
        query="Does RAG always require a vector database?",
        brief={"depth": "deep"},
        notes="- [1] BM25 can match dense retrieval.",
        citations=[{"n": 1, "title": "RAG survey", "url": "https://arxiv.org/abs/x"}],
        min_words=3200,
        comparison_rule="Omit Comparison.\n",
        prior_note="",
        dimension_list="- Retrieval substrate",
    )
    assert "3200" in prompt
    assert "Do not wrap the memo in JSON" in prompt
    assert "Key findings" in prompt
    assert "Contradictions & debates" in prompt
    assert "Worked example" in prompt
    assert "Metric gaps" in prompt
    assert "Research plan" not in prompt.split("Anti-redundancy")[0]
    # Forbid process sections in the required outline (anti-redundancy may mention them).
    outline = prompt.split("Required sections")[-1]
    assert "## Research plan" not in outline
    assert "## Key findings" in outline or "Key findings" in prompt
    assert "no ASCII" in writer_system() or "no ASCII art" in writer_system()
    assert writer_system().startswith("You are Kiln's research writer")
    assert "NEVER prefix" in writer_system() or "[DIRECT]" in writer_system()
    assert "Label load-bearing" not in prompt
    assert "outcome" in prompt.lower() or "ISL/OSL" in prompt


def test_llm_report_uses_markdown_body_when_writer_returns_prose():
    filler = " ".join(["detail"] * 420)
    memo = (
        "# RAG without a vector DB\n\n"
        "## At a glance\n\nLexical retrieval can suffice for many corpora.\n\n"
        f"## Executive summary\n\nLexical retrieval can be enough. {filler} [1]\n\n"
        "## Key findings\n\n1. BM25 remains competitive on structured corpora. [1]\n\n"
        "## Detailed analysis\n\n### Retrieval substrate\n\nBM25 remains competitive. [1]\n\n"
        "### Vector stores\n\nNot mandatory for every RAG stack. [1]\n\n"
        "## Decision rule\n\nStart with hybrid BM25. [1]\n\n"
        "## References\n\n[1] example.\n"
    )

    class Stub:
        available = True
        mode = "gemini"
        last_tokens = 100
        last_error = None
        _slots = [object()]

        def generate(self, prompt, system="", max_tokens=2048, json_mode=False):
            if "CLEAN applied-AI research notes" in system or "compress applied-AI research notes" in system:
                return "## Findings\n- [1] BM25 can match dense retrieval on this corpus."
            return memo

        def generate_json(self, prompt, system="", max_tokens=4096):
            return {
                "title": "RAG without a vector DB",
                "executive_summary": "Lexical retrieval can be enough.",
                "decision_rule": "Start with hybrid BM25.",
                "limitations": ["Corpus is English-only."],
                "open_questions": [],
                "claims": [
                    {
                        "id": "C1",
                        "text": "BM25 can match dense retrieval.",
                        "quote": "BM25 can match dense retrieval.",
                        "url": "https://docs.example.org/0",
                        "tier": "standard_body",
                        "support_ids": [],
                        "contradict_ids": [],
                        "confidence": 0.7,
                        "caveats": [],
                    }
                ],
            }

    token = bind_llm(Stub())
    try:
        report = _llm_report(
            {
                "query": "Does RAG always require a vector database?",
                "brief": {"depth": "standard", "goal": "Does RAG always require a vector database?"},
                "plan": {},
                "budget": {"iterations": 1, "max_iterations": 4},
                "critic": {"status": "sufficient", "coverage": {}},
            },
            [
                {
                    "id": "e1",
                    "title": "Source 0",
                    "url": "https://docs.example.org/0",
                    "quote": "BM25 can match dense retrieval.",
                    "snippet": "BM25 can match dense retrieval.",
                    "tier": "standard_body",
                }
            ],
            [
                {
                    "n": 1,
                    "evidence_id": "e1",
                    "url": "https://docs.example.org/0",
                    "title": "Source 0",
                    "quote": "BM25 can match dense retrieval.",
                }
            ],
            {"sources": 1},
        )
    finally:
        reset_llm(token)

    assert report is not None
    assert report.body_markdown.startswith("# RAG without a vector DB")
    assert report.metrics.get("writer") == "markdown"
    assert "Claim ledger" not in report.body_markdown
