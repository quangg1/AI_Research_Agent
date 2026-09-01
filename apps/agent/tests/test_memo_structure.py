from app.report.memo_structure import consolidate_memo_structure, merge_inline_citations


def test_strip_nested_contradictions_from_analysis():
    md = """## Detailed analysis

### Mechanism
Core claim [1 peer].

### Contradictions and open questions
H1 vs H2 restated here.

## Contradictions & debates

Real debate home [2 peer].
"""
    out = consolidate_memo_structure(md)
    analysis = out.split("## Contradictions & debates")[0]
    assert "Contradictions and open questions" not in analysis
    assert "## Contradictions & debates" in out


def test_merge_metric_gaps_into_uncertainties():
    md = """## Quantitative findings

| Metric | Value |
|---|---|
| Accuracy | 62% [1] |

### Metric gaps
- Latency not reported
- FLOPs not reported

## Uncertainties & gaps

Field still evolving.

## Limitations

Run limits.
"""
    out = consolidate_memo_structure(md)
    assert "### Metric gaps" not in out
    assert "Measurement gaps" in out
    assert "Latency not reported" in out
    assert "Field still evolving" in out


def test_sanitize_uncited_decision_thresholds():
    md = """## Decision rule

### Empirical cutoffs
- Verifiable domain: allow 60%-85% synthetic data.
- Non-verifiable domain: keep synthetic below 15%.

### Engineering heuristics
- Prefer sandbox filtering when quality is unknown.
"""
    out = consolidate_memo_structure(md)
    assert "Heuristic" in out or "Re-benchmark" in out
    assert "60%-85%" not in out or "Heuristic" in out


def test_merge_inline_citations():
    assert merge_inline_citations("Claim [1 peer][8 peer][9 peer] here.") == (
        "Claim here [1, 8, 9 peer]"
    )


def test_merge_duplicate_source_quality_bullets():
    md = """## Source quality

- Band A papers (Band A): Includes surveys [1 peer] [8 peer].
- Band A papers (Band A): Includes benchmarks [9 peer] [6 peer].

## Limitations

Run limits.
"""
    out = consolidate_memo_structure(md)
    sq = out.split("## Limitations")[0]
    assert sq.count("Band A papers") == 1
    assert "[1, 6, 8, 9 peer]" in sq


def test_sanitize_qualitative_quantitative_rows():
    md = """## Quantitative findings

| Metric | Value | Source |
|---|---|---|
| Accuracy | 62% [1] | bench |
| Hallucination control | Eliminates entity hallucinations | [2] |

## Contradictions & debates

None.
"""
    out = consolidate_memo_structure(md)
    quant = out.split("## Contradictions")[0]
    assert "62%" in quant
    assert "Eliminates entity hallucinations" not in quant
