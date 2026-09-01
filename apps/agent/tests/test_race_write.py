from app.report.race_write import (
    destack_inline_citations,
    memo_looks_truncated,
    missing_sections,
    polish_citations,
    race_criteria,
)


def test_destack_citations():
    md = "Latency improved [1][2][3][4] on the benchmark."
    out = destack_inline_citations(md)
    assert out.count("[") == 1
    assert "1, 2, 3, 4" in out
    assert "Latency improved" in out


def test_polish_merges_duplicate_source_quality_bullets():
    md = """## Source quality

- High-Confidence Peer-Reviewed Papers (Band A): Includes core surveys [1 peer] [8 peer].
- High-Confidence Peer-Reviewed Papers (Band A): Includes follow-on work [9 peer] [6 peer].

## References

[1] Paper.
"""
    out = polish_citations(md)
    assert out.lower().count("high-confidence peer-reviewed papers") == 1
    assert "[1, 6, 8, 9 peer]" in out


def test_truncation_detects_missing_sections():
    md = "# Title\n\n## Executive summary\n\nPartial answer only."
    assert memo_looks_truncated(md)
    assert "Decision rule" in missing_sections(md)


def test_truncation_complete_memo():
    body = " ".join(["word"] * 420)
    md = f"""# Title

## At a glance
One line summary.

## Executive summary
Full answer here with enough words. {body}

## Key findings
1. Finding one [1]

## Detailed analysis
### Mechanism
Details here with analysis.

### Bottleneck
More details here.

## Decision rule
Do X when Y.

## References
[1] paper.
"""
    assert not memo_looks_truncated(md)


def test_race_criteria_includes_must_answer():
    crit = race_criteria("How does vLLM batch requests for LoRA adapters?", {"depth": "deep"})
    assert crit["min_words"] == 5500
    assert any("vLLM" in x or "LoRA" in x or "batch" in x.lower() for x in crit["comprehensiveness"])


def test_polish_strips_hrules_and_visual_section():
    md = (
        "## Source quality\n\nBand A [1 peer].\n\n---\n\n"
        "## Visual summary & code\n\n```python\nprint('x')\n```\n\n"
        "## References\n\n[1] Paper"
    )
    out = polish_citations(md)
    assert "---" not in out
    assert "Visual summary" not in out
    assert "## References" in out
