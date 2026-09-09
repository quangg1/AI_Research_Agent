"""Regression: URL Reference tails must not discard LLM memos as truncated."""

from __future__ import annotations

from app.report.race_write import memo_looks_truncated, _tail_looks_complete


def _fat_memo(*, end: str = "https://arxiv.org/abs/2305.14314") -> str:
    pad = (
        "Additional grounded discussion of adapter ranks, quantization noise, "
        "optimizer paging, and domain-shift failure modes appears across primary "
        "papers and vendor guides for practitioners choosing fine-tuning methods. "
    )
    body = f"""# LoRA vs QLoRA
## At a glance
Prefer QLoRA under memory pressure; re-benchmark domain quality before locking the choice.
## Executive summary
This memo compares full-parameter fine-tuning, LoRA, and QLoRA on domain tasks with selection criteria for memory, speed, and quality.
## Key findings
1. QLoRA reduces peak memory versus LoRA.
2. LoRA is often faster to train than QLoRA.
3. Full fine-tuning can win on hard domain shifts when data is abundant.
## Detailed analysis
### Performance
{pad}{pad}
### Memory
{pad}{pad}
### Cost
{pad}
### Generalization
{pad}
## Decision rule
Prefer QLoRA under GPU memory pressure; prefer LoRA when throughput dominates; prefer full FT when the quality gap is measured.
## References
- [QLORA]({end}) — `{end}`
"""
    while len(body.split()) < 420:
        body = body.replace("## Detailed analysis", "## Detailed analysis\n" + pad, 1)
    return body


def test_url_ending_memo_is_not_truncated():
    body = _fat_memo()
    assert _tail_looks_complete(body)
    assert not memo_looks_truncated(body)


def test_missing_decision_rule_still_truncated():
    body = _fat_memo().replace("## Decision rule", "## Rules")
    assert memo_looks_truncated(body)


def test_missing_at_a_glance_still_truncated():
    """Writer contract requires ## At a glance; UI may also derive the field, but body QA stays strict."""
    body = _fat_memo().replace("## At a glance\nPrefer QLoRA under memory pressure; re-benchmark domain quality before locking the choice.\n", "")
    assert memo_looks_truncated(body)
