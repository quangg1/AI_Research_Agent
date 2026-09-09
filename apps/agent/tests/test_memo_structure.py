import re

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


def test_strips_content_free_ascii_diagram():
    """writer_system() says 'no ASCII art diagrams' but the model doesn't always
    obey (real memo output: a fenced block of nothing but arrows/whitespace)."""
    md = """## Worked example

Intro sentence [1 peer].

```
 -> ->
 |
 <- <- <-
```

### Step 1: Real step
Substantive content [2 peer].
"""
    out = consolidate_memo_structure(md)
    assert "```" not in out
    assert "->" not in out
    assert "Substantive content" in out


def test_strips_non_empty_fenced_diagram_too():
    """writer_system() bans fenced code blocks unconditionally, not just
    content-free ones — a real memo produced an elaborate (and misaligned)
    box diagram whose content just duplicated nearby prose. The rule has no
    "but this one has real words" exception, so neither does the stripper."""
    md = """## Detailed analysis

Multi-agent expansion generates instructions from seed data [1 peer].

```
Raw Domain Corpus
│
▼
┌─────────────────────────┐
│ Multi-Agent Expansion │
└─────────────────────────┘
```

### Step 1: Real step
Substantive content [2 peer].
"""
    out = consolidate_memo_structure(md)
    assert "```" not in out
    assert "Multi-Agent Expansion" not in out
    assert "Substantive content" in out


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


def test_source_quality_rebuilt_from_ledger_when_citations_given():
    """Regression: two consecutive real memos left a Source quality band's
    citation group empty ("Band B — Specialist ...: ") even though those
    tiers were cited throughout the body — the LLM just doesn't reliably
    fill this section in. When citations are available, rebuild it instead
    of trying to repair whatever prose the writer produced."""
    md = """## Source quality

- Band A — Peer-Reviewed Publications: [1 peer, 2 peer]
- Band B — Specialist Technical Reports & Preprints:
- Band C — Industry Whitepapers & Commercial Documentation:

## References
"""
    citations = [
        {"n": 1, "url": "https://doi.org/1", "tier": "peer_reviewed"},
        {"n": 2, "url": "https://doi.org/2", "tier": "peer_reviewed"},
        {"n": 3, "url": "https://arxiv.org/html/1", "tier": "specialist_research"},
        {"n": 4, "url": "https://arxiv.org/html/2", "tier": "specialist_research"},
    ]
    out = consolidate_memo_structure(md, citations=citations)
    sq = out.split("## References")[0]
    assert "[1 peer, 2 peer]" in sq
    assert "[3 preprint, 4 preprint]" in sq
    assert not re.search(r":\s*\n", sq)


def test_source_quality_inserted_when_missing_entirely():
    """Regression: a real deep-tier memo (which requires this section per
    deep_write.py's prompt) had no "## Source quality" heading anywhere in
    the body at all — not incomplete, just absent. The repair-only path
    above can't fix a section that was never written; insert one instead."""
    md = """## Detailed analysis

Some content [1 specialist].

## References

1. Paper A
"""
    citations = [{"n": 1, "url": "https://arxiv.org/html/1", "tier": "specialist_research"}]
    out = consolidate_memo_structure(md, citations=citations)
    assert "## Source quality" in out
    assert "[1 specialist]" in out.split("## References")[0]


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
