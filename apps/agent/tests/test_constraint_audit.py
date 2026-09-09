"""Standalone tests for constraint_audit (Kiln Phase B). No graph/psycopg."""

from __future__ import annotations

from app.domain.constraint_audit import apply_constraint_audit, audit_memo_against_contract
from app.domain.research_contract import compile_research_contract

QUERY_7B = (
    "Peak VRAM / training memory footprints of LoRA vs QLoRA on 7B models; "
    "prefer primary sources (Hu LoRA, Dettmers QLoRA)."
)


def _memo_with_offscope() -> str:
    return """# Research Memo

## Key findings

- QLoRA saves memory on 7B models [1].

## Detailed analysis

### Class-rebalanced preference adaptation

DPO and ORPO improve mental-health clinical F1; abstention accuracy reaches 0.998 on email QA [2].

## Decision rule

Prefer QLoRA when VRAM is tight.

## Source quality

- **[1]** QLoRA paper
- **[2]** Preference paper

## References

- **[1]** [QLoRA](https://arxiv.org/abs/2305.14314)
- **[2]** [Pref](https://arxiv.org/abs/2604.00773)
"""


def test_audit_records_exclude_and_mandatory_gaps():
    contract = compile_research_contract(QUERY_7B, {"must_cover": ["VRAM"]})
    body = _memo_with_offscope()
    out = audit_memo_against_contract(
        body,
        contract,
        query=QUERY_7B,
        citations=[{"n": 1, "url": "https://arxiv.org/abs/2305.14314", "title": "QLoRA"}],
        evidence=[
            {
                "url": "https://arxiv.org/abs/2305.14314",
                "title": "QLoRA",
                "snippet": "7B QLoRA peak VRAM 8-10 GB NF4",
                "full_text": "QLoRA 7B peak training VRAM 8-10 GB with NF4 bitsandbytes.",
            }
        ],
    )
    assert out["had_contract"] is True
    assert out["gaps"]
    # Hu LoRA mandatory still missing
    assert any("2106.09685" in g or "Hu" in g for g in out["gaps"])
    assert out.get("hard_fail") is True
    assert out.get("should_block_publish") is True
    # Uncertainties section should appear
    assert "## Uncertainties & gaps" in out["body_markdown"]
    # demote should have fired on preference subsection for memory query
    assert any("off_scope" in f for f in out["flags"]) or "Demoted" in out["body_markdown"]
    # References markers must not become ****
    assert "****" not in out["body_markdown"]
    assert "**[1]**" in out["body_markdown"]


def test_apply_constraint_audit_fail_soft_without_contract():
    out = apply_constraint_audit(
        "## Key findings\n\n- hello\n",
        query="",
        brief={},
        state={},
    )
    assert out.get("skipped") is True or out.get("had_contract") is False
    assert out["body_markdown"].startswith("## Key findings")


def test_author_repo_identity_gap_for_qlora():
    contract = compile_research_contract(QUERY_7B, {})
    body = """## Key findings

- QLoRA reduces peak VRAM on 7B models versus LoRA.

## Uncertainties & gaps

- TBD
"""
    out = audit_memo_against_contract(body, contract, query=QUERY_7B, citations=[], evidence=[])
    joined = " ".join(out["gaps"]).lower()
    assert "dettmers" in joined or "artidoro" in joined or "mandatory" in joined
