"""Residual memo_gate / bind / quality regressions (standalone, no graph)."""

from __future__ import annotations

from app.domain.citations import (
    _cited_numbers,
    annotate_inline_citation_tiers,
    bind_markdown_to_ledger,
    format_source_quality_section,
)
from app.domain.memo_quality import (
    check_memo_quality,
    declutter_citations,
    _detect_citation_stacking,
)


def test_cited_numbers_counts_tiered_markers():
    md = """Prose uses [1 peer] and [2, 3 preprint] only.

## Source quality
- **Peer-Reviewed Publications**: [9 peer]

## References
- **[1]** should not define cited-set alone
"""
    assert sorted(_cited_numbers(md)) == [1, 2, 3]


def test_bind_keeps_tiered_only_prose_cites():
    md = """# T

Body cites [1 peer] and [2].

## Source quality

- **Peer-Reviewed Publications**: 

## References

- old
"""
    citations = [
        {
            "n": 1,
            "title": "QLoRA",
            "url": "https://arxiv.org/abs/2305.14314",
            "tier": "specialist_research",
        },
        {
            "n": 2,
            "title": "ACL",
            "url": "https://aclanthology.org/2022.findings-emnlp.534",
            "tier": "peer_reviewed",
        },
    ]
    out = bind_markdown_to_ledger(md, citations)
    assert "**[1]**" in out
    assert "**[2]**" in out
    assert "1 preprint" in out or "1 specialist" in out or "Specialist" in out
    assert "2 peer" in out
    assert "****" not in out


def test_format_source_quality_includes_unbanded_tiers():
    out = format_source_quality_section(
        [
            {"n": 1, "title": "A", "url": "https://arxiv.org/abs/2305.14314", "tier": ""},
            {"n": 2, "title": "B", "url": "https://aclanthology.org/x", "tier": "peer_reviewed"},
        ]
    )
    assert "Peer-Reviewed Publications" in out
    assert "2 peer" in out
    assert "Other sources" in out
    assert "[1]" in out


def test_bind_rebuilds_empty_bands_even_without_tiers():
    md = """# T

Cites [1] and [2].

## Source quality

- **Peer-Reviewed Publications**: 
- **Specialist Research & Preprints**: 

## References

- old
"""
    citations = [
        {"n": 1, "title": "A", "url": "https://arxiv.org/abs/2305.14314", "tier": ""},
        {"n": 2, "title": "B", "url": "https://arxiv.org/abs/2106.09685", "tier": ""},
    ]
    out = bind_markdown_to_ledger(md, citations)
    assert "Other sources" in out
    assert "[1" in out and "[2" in out or "1, 2" in out
    assert "****" not in out
    # empty stub labels should be gone
    assert "Peer-Reviewed Publications**: \n" not in out


def test_source_quality_bands_do_not_trigger_stacking_regen():
    body = """# Title

## Executive summary

Hello world ends here.

## Detailed analysis

### A

Some fine prose with two cites only [1, 2].

## Source quality

- **Peer-Reviewed Publications**: [1 peer, 2 peer, 3 peer].
- **Specialist Research & Preprints**: [4 preprint, 5 preprint, 6 preprint].

## References

- **[1]** [Is Model Collapse Inevitable?](https://example.com/a) — `https://example.com/a`
- **[2]** [Paper 2021, 2022, 2023](https://example.com/b) — `https://example.com/b`
"""
    stack_count, _ = _detect_citation_stacking(body)
    assert stack_count == 0
    qc = check_memo_quality(body)
    assert qc["should_regenerate"] is False
    assert not any("Citation stacking" in i for i in qc["issues"])


def test_annotate_does_not_mutate_reference_markers():
    refs_md = (
        "# T\n\nProse [7].\n\n## References\n\n"
        + ("padding " * 30)
        + "\n- **[7]** [T](https://arxiv.org/abs/2305.14314) — `https://arxiv.org/abs/2305.14314`\n"
    )
    out = annotate_inline_citation_tiers(
        refs_md,
        [
            {
                "n": 7,
                "title": "T",
                "url": "https://arxiv.org/abs/2305.14314",
                "tier": "specialist_research",
            }
        ],
    )
    assert "**[7]**" in out
    assert "**[7 preprint]**" not in out
    assert "Prose [7 preprint]" in out or "Prose [7 specialist]" in out


def test_declutter_still_preserves_sections_after_fixes():
    body = """# Title
## At a glance
Prefer QLoRA.
## Executive summary
LoRA and QLoRA differ [1, 2, 3, 4].
## Detailed analysis
### Memory
QLoRA uses less peak VRAM [1, 2, 3].
## Source quality
- **Specialist Research & Preprints**: [4 preprint]
## References
- **[4 preprint]** [Profiling](https://arxiv.org/html/2509.12229v1) — `https://arxiv.org/html/2509.12229v1`
"""
    out, changed = declutter_citations(body)
    assert "## Detailed analysis" in out
    assert "**[4 preprint]**" in out
    assert "****" not in out
    assert changed > 0

def test_off_topic_strip_does_not_blank_reference_markers():
    """Live ff3c5688: relevance false-positives + global [n] strip -> **** refs."""
    from app.domain.report_integrity import enforce_report_integrity

    md = """# T

## At a glance
Prefer QLoRA.

## Executive summary
QLoRA cuts VRAM vs LoRA [1 primary, 6 repo].

## Key findings
- Memory [1 primary]

## Detailed analysis
### Memory
Base weights stay 4-bit [1 primary, 6 repo]. Extra note [2 primary].

## Decision rule
- Use QLoRA under VRAM pressure [2 primary]

## Source quality
- **Official Documentation & Standards**: [1 primary, 2 primary]
- **Specialist Research & Preprints**: [6 specialist]
- **Other sources**: 

## References
- **[1]** [4-bit](https://huggingface.co/docs/bitsandbytes/reference/nn/linear4bit) — https://huggingface.co/docs/bitsandbytes/reference/nn/linear4bit
- **[2]** [LoRA](https://huggingface.co/docs/peft/main/en/conceptual_guides/lora) — https://huggingface.co/docs/peft/main/en/conceptual_guides/lora
- **[6]** [qlora](https://github.com/artidoro/qlora) — https://github.com/artidoro/qlora
"""
    citations = [
        {
            "n": 1,
            "title": "4-bit",
            "url": "https://huggingface.co/docs/bitsandbytes/reference/nn/linear4bit",
            "tier": "official_regulation",
            "snippet": "4-bit quantization fine-tuning LLM",
        },
        {
            "n": 2,
            "title": "LoRA",
            "url": "https://huggingface.co/docs/peft/main/en/conceptual_guides/lora",
            "tier": "official_regulation",
            "snippet": "LoRA fine-tuning large language models",
        },
        {
            "n": 6,
            "title": "qlora",
            "url": "https://github.com/artidoro/qlora",
            "tier": "specialist_research",
            "snippet": "Efficient Finetuning of Quantized LLMs",
        },
    ]
    evidence = [
        {"url": c["url"], "title": c["title"], "snippet": c["snippet"], "text": c["snippet"]}
        for c in citations
    ]
    query = (
        "Compare peak VRAM of LoRA versus QLoRA for 7B models using primary sources"
    )
    out = enforce_report_integrity(
        body_markdown=md,
        executive_summary="",
        decision_rule="",
        at_a_glance="",
        citations=citations,
        critic={},
        limitations=[],
        evidence=evidence,
        query=query,
    )
    body = out["body_markdown"]
    assert "****" not in body
    assert "**[1]**" in body
    assert "**[2]**" in body
    assert "**[6]**" in body
    # Empty Other sources stub must not survive a ledger rebuild.
    assert "- **Other sources**:" not in body or "- **Other sources**: [" in body


def test_hard_off_topic_strip_skips_protected_sections():
    from app.domain.report_integrity import enforce_report_integrity

    md = """# T

Prose cites an off-topic paper [9] about the topic.

## Decision rule
- Ignore [9]

## Source quality
- **Other sources**: [9]

## References
- **[9]** [Brain injury](https://example.com/neuro) — https://example.com/neuro
"""
    citations = [
        {
            "n": 9,
            "title": "Brain injury",
            "url": "https://example.com/neuro",
            "tier": "",
            "snippet": "neural regeneration brain injury patient neuroscience medical",
        }
    ]
    evidence = [
        {
            "url": "https://example.com/neuro",
            "title": "Brain injury",
            "snippet": "neural regeneration brain injury patient neuroscience medical",
            "text": "neural regeneration brain injury patient neuroscience medical",
        }
    ]
    out = enforce_report_integrity(
        body_markdown=md,
        executive_summary="",
        decision_rule="",
        at_a_glance="",
        citations=citations,
        critic={},
        limitations=[],
        evidence=evidence,
        query="Compare LoRA versus QLoRA VRAM for 7B LLMs",
    )
    body = out["body_markdown"]
    assert "****" not in body
    assert any("off_topic_citation_stripped_9" == f for f in out["integrity_flags"])
    # Prose marker removed; protected list either rebuilt without 9 or kept intact
    # without **** corruption.
    assert "Prose cites an off-topic paper [9]" not in body


def test_relevance_weak_overlap_does_not_reject_lora_docs():
    from app.domain.citation_relevance import check_citation_relevance

    ok, issues = check_citation_relevance(
        {
            "title": "LoRA",
            "url": "https://huggingface.co/docs/peft/main/en/conceptual_guides/lora",
            "snippet": "Low-Rank Adaptation of large language models fine-tuning",
        },
        "Compare LoRA versus QLoRA VRAM for 7B models",
        strict=True,
    )
    assert ok is True


def test_memo_not_truncated_without_references_heading():
    """Missing ## References alone is not truncation — bind rebuilds it."""
    from app.report.race_write import memo_looks_truncated

    md = """# Title
## At a glance
Prefer QLoRA under memory pressure for 7B training.
## Executive summary
QLoRA reduces peak VRAM versus 16-bit LoRA for 7B models by quantizing base weights.
## Key findings
- QLoRA uses 4-bit NF4 base weights.
- LoRA keeps 16-bit base weights in VRAM.
## Detailed analysis
### Memory footprint
QLoRA stores base weights in 4-bit while adapters stay 16-bit, cutting static weight VRAM.
### Throughput tradeoffs
Dequantization adds some overhead but usually fits single-GPU fine-tuning budgets.
## Decision rule
- Prefer QLoRA when peak VRAM is the binding constraint.
"""
    # Pad to clear the word-count floor without relying on References.
    md = md + (" Additional context about adapter rank and optimizer state." * 40)
    assert memo_looks_truncated(md) is False


def test_multi_tier_inline_and_tiered_ref_fixture_no_stars():
    from app.domain.citations import annotate_inline_citation_tiers, bind_markdown_to_ledger
    from app.domain.memo_quality import declutter_citations
    from app.domain.report_integrity import enforce_report_integrity

    md = """# T
## At a glance
Prefer QLoRA.
## Executive summary
See [1 primary, 6 repo] and also [4 preprint].
## Key findings
- x
## Detailed analysis
### Memory
Weights [1 primary, 6 repo]. Pref [5 preprint, 7 specialist].
## Decision rule
- Go QLoRA [2 primary]
## Source quality
- **Official Documentation & Standards**: [1 primary, 2 primary]
- **Other sources**: 
## References
- **[1]** [A](https://huggingface.co/docs/bitsandbytes/reference/nn/linear4bit) — https://huggingface.co/docs/bitsandbytes/reference/nn/linear4bit
- **[2]** [B](https://huggingface.co/docs/peft/main/en/conceptual_guides/lora) — https://huggingface.co/docs/peft/main/en/conceptual_guides/lora
- **[4 preprint]** [C](https://arxiv.org/html/2509.12229v1) — https://arxiv.org/html/2509.12229v1
- **[5]** [D](https://arxiv.org/html/2604.00773v1) — https://arxiv.org/html/2604.00773v1
- **[6]** [E](https://github.com/artidoro/qlora) — https://github.com/artidoro/qlora
- **[7]** [F](https://www.mdpi.com/2076-3417/16/13/6645/pdf?version=1) — https://www.mdpi.com/2076-3417/16/13/6645/pdf?version=1
"""
    citations = [
        {"n": 1, "title": "A", "url": "https://huggingface.co/docs/bitsandbytes/reference/nn/linear4bit", "tier": "official_regulation", "snippet": "quantization fine-tuning"},
        {"n": 2, "title": "B", "url": "https://huggingface.co/docs/peft/main/en/conceptual_guides/lora", "tier": "official_regulation", "snippet": "LoRA fine-tuning"},
        {"n": 4, "title": "C", "url": "https://arxiv.org/html/2509.12229v1", "tier": "specialist_research", "snippet": "LoRA QLoRA fine-tuning"},
        {"n": 5, "title": "D", "url": "https://arxiv.org/html/2604.00773v1", "tier": "specialist_research", "snippet": "preference LoRA QLoRA"},
        {"n": 6, "title": "E", "url": "https://github.com/artidoro/qlora", "tier": "specialist_research", "snippet": "Quantized LLMs fine-tuning"},
        {"n": 7, "title": "F", "url": "https://www.mdpi.com/2076-3417/16/13/6645/pdf?version=1", "tier": "specialist_research", "snippet": "Memory-Efficient Adaptation"},
    ]
    evidence = [{"url": c["url"], "title": c["title"], "snippet": c["snippet"], "text": c["snippet"]} for c in citations]
    bound = bind_markdown_to_ledger(md, citations)
    out = enforce_report_integrity(
        body_markdown=bound,
        executive_summary="",
        decision_rule="",
        at_a_glance="",
        citations=citations,
        critic={},
        limitations=[],
        evidence=evidence,
        query="Compare LoRA versus QLoRA VRAM",
    )["body_markdown"]
    out = annotate_inline_citation_tiers(out, citations)
    out, _ = declutter_citations(out)
    assert "****" not in out
    assert "**[1]**" in out
    assert "- **Other sources**:" not in out or "- **Other sources**: [" in out

def test_entity_strip_preserves_section_headers_when_stripping():
    """splitlines/join must not glue ## headers onto the previous line."""
    from app.domain.report_integrity import _strip_ungrounded_entity_citations

    md = """# T
## At a glance
Prefer QLoRA.
## Executive summary
LangGraph routing is described here [1].
## Key findings
- x
## Detailed analysis
### Memory
CrewAI tooling claims appear here [1].
## Decision rule
- Prefer QLoRA
## References
- **[1]** [Paper](https://example.com/paper) — https://example.com/paper
"""
    citations = [
        {
            "n": 1,
            "title": "Paper",
            "url": "https://example.com/paper",
        }
    ]
    evidence = [
        {
            "url": "https://example.com/paper",
            "title": "Paper",
            # Long enough to pass the <25-char blob guard, but never names the
            # entities on the cited lines.
            "text": "A measurement study of tokenizer fertility and batching throughput on GPUs. " * 3,
        }
    ]
    out, n = _strip_ungrounded_entity_citations(
        md,
        citations=citations,
        evidence=evidence,
        query="Compare LangGraph versus CrewAI and AutoGen agent frameworks",
    )
    assert n >= 1
    for heading in (
        "## At a glance",
        "## Executive summary",
        "## Key findings",
        "## Detailed analysis",
        "## Decision rule",
        "## References",
    ):
        assert heading in out.splitlines(), heading
    assert "ls## " not in out
    assert "t## " not in out

def test_writer_qa_notice_stripped_from_published_body():
    # Standalone: do not import graph.nodes.report (pulls psycopg via retrieval).
    from app.report.memo_structure import consolidate_memo_structure, _strip_writer_qa_banners

    md = """# T
## Executive summary

> **Note:** Writer QA flagged this memo (truncated_unrepaired); kept the LLM draft instead of replacing it with the heuristic template.
Fine-tuning 7B models with QLoRA reduces VRAM.
## Detailed analysis
### Memory
Details.
"""
    assert "Writer QA flagged" not in _strip_writer_qa_banners(md)
    assert "Writer QA flagged" not in consolidate_memo_structure(md)


def test_decision_rule_drops_slot_label_leaks():
    from app.report.memo_structure import consolidate_memo_structure

    md = """## Decision rule
### Empirical cutoffs (sources only)
**Act on these - the sources support them directly:**
- preference-based [2 primary] - from the 8 cited sources.
- Constraints, Limitations, and Failure Modes - from the 8 cited sources.
- Prefer QLoRA when peak VRAM is under 12 GB [1 primary].
### Engineering heuristics (AI suggestion - not from papers)
**Verify before acting - evidence is indirect or single-sourced:**
- Verify: class-rebalanced [2 primary] - weak/single-sourced; confirm with one independent primary source.
- Verify: Direct answer to the question as asked - weak/single-sourced; confirm with one independent primary source.
- Verify: re-benchmark domain quality before locking adapter choice.
"""
    out = consolidate_memo_structure(md)
    assert "from the 8 cited sources" not in out
    assert "Constraints, Limitations, and Failure Modes" not in out
    assert "Direct answer to the question as asked" not in out
    assert "Prefer QLoRA when peak VRAM" in out
    assert "re-benchmark domain quality" in out


def test_comparison_keyword_soup_header_omitted():
    from app.report.memo_structure import consolidate_memo_structure

    md = """## Comparison
| Method / Metric | LoRA | VRAM | BF16 | Strict |
|:--- |:--- |:--- |:--- |:--- |
| **Base Weight Precision** | FP16 | 4-bit | BF16 | FP16 |
"""
    out = consolidate_memo_structure(md)
    assert "VRAM" not in out.split("## Comparison")[1].split("\n")[0:5] or "omitted" in out.lower()
    assert "column headers looked like metric keywords" in out.lower() or "omitted" in out.lower()
    assert "| Method / Metric | LoRA | VRAM | BF16 | Strict |" not in out

