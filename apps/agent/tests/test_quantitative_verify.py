from app.domain.quantitative_verify import (
    audit_quantitative_table,
    number_in_source,
    repair_quantitative_table,
    sanitize_quantitative_table,
    semantic_number_grounded,
    verify_quantitative_row,
)

# Real Table III text from arXiv:2603.22651 ("Benchmarking Multi-Agent LLM
# Architectures for Financial Document Processing"), as actually retrieved.
FINANCIAL_TABLE_SOURCE = (
    "TABLE III: Primary benchmark results. Best value per metric column shown "
    "in bold. Architecture Metric GPT-4o Claude 3.5S Gemini 1.5P Llama3 70B "
    "Mixtral 8x22B Sequential (A) F1 0.897 0.903 0.881 0.834 0.812 Doc Acc "
    "0.631 0.648 0.597 0.487 0.453 Lat (s) 34.2 38.7 29.1 22.4 19.8 Cost ($) "
    "0.142 0.187 0.098 0.038 0.031 Parallel (B) F1 0.908 0.914 0.893 0.851 "
    "0.829 Doc Acc 0.659 0.672 0.623 0.521 0.488 Hierarchical (C) F1 0.921 "
    "0.929 0.907 0.869 0.843 Doc Acc 0.704 0.718 0.662 0.558 0.519 Reflexive "
    "(D) F1 0.936 0.943 0.919 0.878 0.851 Doc Acc 0.741 0.758 0.691 0.572 0.534"
)

# Real text from arXiv:2606.05670 ("Do More Agents Help?") — one system's two
# result granularities, not a single-agent-vs-multi-agent comparison.
GAIA_SOURCE = (
    "Under SI conditions, at most one of six tested MAS exceeds the matched "
    "single-agent anchor on benchmark-balanced average accuracy, while the "
    "remaining five trail by 2.56-11.29 points. On the PAE GAIA snapshot, a "
    "Claude-Code-style runtime workflow reaches 66.72% overall and 69.23% on "
    "Level 3, more than 20 points above the strongest non-Claude baseline."
)

COUNCIL_SOURCE = (
    "HaluEval hallucination rate results on our benchmark suite. "
    "Single Model (No Web) achieves 10.8% HR. RAG Baseline 8.5%. "
    "Council (No Web) 7.0%. Council + Web 4.5%. "
    "Relative reduction of 41.7% compared to single model baseline."
)

TOOL_SOURCE = (
    "On tau2-bench Airline domain, standard RL reaches 58.0% task accuracy "
    "while verifiable RL with synthetic trajectories reaches 73.0%. "
    "Telecom domain: 53.7% to 98.3%."
)


def test_number_in_source_handles_decimal_variants():
    assert number_in_source("10.8%", COUNCIL_SOURCE)
    assert number_in_source("7.0%", COUNCIL_SOURCE)
    assert not number_in_source("38.60%", COUNCIL_SOURCE)
    assert not number_in_source("12.20%", COUNCIL_SOURCE)


def test_rejects_council_mode_confabulated_row():
    row = {
        "line": (
            "| Council Mode | 38.60% HR (Single) | 12.20% HR (Council) | "
            "-26.40pp | Council [8 peer] |"
        ),
        "cells": ["Council Mode", "38.60% HR (Single)", "12.20% HR (Council)", "-26.40pp", "Council [8 peer]"],
        "cite_n": 8,
        "numbers": ["38.60%", "12.20%", "-26.40pp"],
    }
    citations = [{"n": 8, "url": "https://arxiv.org/abs/2604.02923", "title": "Council Mode"}]
    evidence = [{"url": "https://arxiv.org/abs/2604.02923", "full_text": COUNCIL_SOURCE}]
    result = verify_quantitative_row(row, citations=citations, evidence=evidence)
    assert result["status"] == "wrong_number"
    assert "38.60%" in result["missing"] or "12.20%" in result["missing"]


def test_keeps_grounded_council_row():
    row = {
        "line": "| Council (No Web) | 10.8% | 7.0% | -3.8pp | Council [8 peer] |",
        "cells": ["Council (No Web)", "10.8%", "7.0%", "-3.8pp", "Council [8 peer]"],
        "cite_n": 8,
        "numbers": ["10.8%", "7.0%"],
    }
    citations = [{"n": 8, "url": "https://arxiv.org/abs/2604.02923"}]
    evidence = [{"url": "https://arxiv.org/abs/2604.02923", "full_text": COUNCIL_SOURCE}]
    result = verify_quantitative_row(row, citations=citations, evidence=evidence)
    assert result["status"] == "verified"


def test_rejects_cross_cell_misread_from_multi_condition_table():
    """Regression: a real memo cited "0.878 (Sequential baseline)" — 0.878 is
    genuinely in the source, so number_in_source alone passes it, but Table
    III lists 0.878 under Reflexive x Llama3, not Sequential x Claude (the
    real Sequential x Claude value is 0.903). number_in_source can't catch a
    right-number-wrong-cell misread; semantic_number_grounded must."""
    cells = [
        "Field-Level F1 Score",
        "0.878 (Sequential baseline)",
        "0.943 (Reflexive) / 0.929 (Hierarchical)",
        "+0.065 F1 delta (Reflexive); Hierarchical retains 98.5% of Reflexive F1",
        "Financial Document Extraction (Claude 3.5 Sonnet)",
        "[3]",
    ]
    row = {
        "line": "| " + " | ".join(cells) + " |",
        "cells": cells,
        "cite_n": 3,
        "numbers": ["0.878", "0.943", "0.929"],
    }
    ok, note = semantic_number_grounded(row, FINANCIAL_TABLE_SOURCE)
    assert not ok
    assert "0.878" in note


def test_keeps_correctly_attributed_multi_condition_numbers():
    """The same table's 0.943 (Reflexive) and 0.929 (Hierarchical) are the
    real values for those architectures — must not be flagged just because
    they share a row with a misattributed figure."""
    for cells, num in [
        (["0.943 (Reflexive)"], "0.943"),
        (["0.929 (Hierarchical)"], "0.929"),
    ]:
        row = {"line": cells[0], "cells": cells, "cite_n": 3, "numbers": [num]}
        ok, note = semantic_number_grounded(row, FINANCIAL_TABLE_SOURCE)
        assert ok, note


def test_rejects_relational_fabrication_from_single_system_metrics():
    """Regression: a real memo framed "66.72% overall and 69.23% on Level 3"
    (two granularities of ONE system) as "66.72% (single agent) vs 69.23%
    (multi-agent)" — a comparison invented to support the memo's own thesis.
    Both numbers are individually real, so number_in_source passes both."""
    cells = [
        "Overall Task Pass@1 Accuracy (%)",
        "66.72% (Protocol-aligned single agent)",
        "69.23% (Multi-agent workflow)",
        "+2.51 percentage points (within Wilson 95% CI noise bound)",
        "GAIA Benchmark (Generalist AI Tasks)",
        "[4]",
    ]
    row = {
        "line": "| " + " | ".join(cells) + " |",
        "cells": cells,
        "cite_n": 4,
        "numbers": ["66.72%", "69.23%"],
    }
    ok, note = semantic_number_grounded(row, GAIA_SOURCE)
    assert not ok


def test_sanitize_strips_confabulated_rows_from_memo():
    memo = (
        "## Quantitative findings\n\n"
        "| Domain | Baseline | Treatment | Delta | Source |\n"
        "|---|---|---|---|---|\n"
        "| Council Mode | 38.60% | 12.20% | -26.40pp | Council [8 peer] |\n"
        "| Council real | 10.8% | 7.0% | -3.8pp | Council [8 peer] |\n\n"
        "## Worked example\n\nStep 1.\n"
    )
    citations = [{"n": 8, "url": "https://arxiv.org/abs/2604.02923"}]
    evidence = [{"url": "https://arxiv.org/abs/2604.02923", "full_text": COUNCIL_SOURCE}]
    cleaned, stats = sanitize_quantitative_table(memo, citations=citations, evidence=evidence)
    assert stats["removed"] == 1
    assert stats["kept"] == 1
    assert "38.60%" not in cleaned
    assert "10.8%" in cleaned
    assert "Council real" in cleaned


def test_audit_flags_ungrounded_tool_use_row():
    memo = (
        "## Quantitative findings\n\n"
        "| Domain | Baseline | Treatment | Delta | Source |\n"
        "|---|---|---|---|---|\n"
        "| Tool-Use | 54.30% | 71.80% | +17.50pp | Agent [7 peer] |\n"
    )
    citations = [{"n": 7, "url": "https://arxiv.org/abs/2601.22607"}]
    evidence = [{"url": "https://arxiv.org/abs/2601.22607", "full_text": TOOL_SOURCE}]
    notes = audit_quantitative_table(memo, citations=citations, evidence=evidence)
    assert notes
    assert any("54.30" in n or "71.80" in n for n in notes)


def test_repair_splits_joined_table_rows():
    memo = (
        "## Quantitative findings\n\n"
        "| A | B |\n|---|---|\n"
        "| one | 1 || two | 2 |\n\n"
        "## Worked example\n\nx\n"
    )
    fixed = repair_quantitative_table(memo)
    assert "||" not in fixed
    assert "| one | 1 |" in fixed
    assert "| two | 2 |" in fixed


def test_rejects_ungrounded_multiplier_row():
    row = {
        "line": "| Synth | 15x | MMLU | [3 preprint] |",
        "cells": ["Synth", "15x", "MMLU", "[3 preprint]"],
        "cite_n": 3,
        "numbers": ["15x"],
    }
    citations = [{"n": 3, "url": "https://arxiv.org/abs/1234.5678"}]
    evidence = [{
        "url": "https://arxiv.org/abs/1234.5678",
        "full_text": (
            "We observe modest gains on MMLU and related reasoning benchmarks in our ablation study. "
            "No multiplicative speedup factor was reported in the published results."
        ),
    }]
    result = verify_quantitative_row(row, citations=citations, evidence=evidence)
    assert result["status"] == "wrong_number"


def test_sanitize_removes_filler_quant_rows():
    memo = (
        "## Quantitative findings\n\n"
        "| Source | Metric | Value |\n|---|---|---|\n"
        "| [3 preprint] | 15x | unverified benchmark |\n\n"
        "## Worked example\n\nStep.\n"
    )
    citations = [{"n": 3, "url": "https://arxiv.org/abs/1234.5678"}]
    evidence = [{"url": "https://arxiv.org/abs/1234.5678", "full_text": "Qualitative discussion only."}]
    cleaned, stats = sanitize_quantitative_table(memo, citations=citations, evidence=evidence)
    assert stats["removed"] >= 1
    assert "15x" not in cleaned
    assert "could not be verified" in cleaned.lower() or "No numeric results" in cleaned
