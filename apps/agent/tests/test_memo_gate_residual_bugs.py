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
