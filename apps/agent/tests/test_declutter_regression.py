"""Regressions for memo_gate declutter wiping sections / blanking refs."""

from __future__ import annotations

from app.domain.memo_quality import declutter_citations
from app.domain.citations import bind_markdown_to_ledger, format_reference_list


def test_declutter_keeps_detailed_analysis_and_reference_markers():
    body = """# Title
## At a glance
Prefer QLoRA under memory pressure.
## Executive summary
LoRA and QLoRA differ on memory and speed [1, 2, 3, 4].
## Detailed analysis
### Memory
QLoRA uses less peak VRAM [1, 2, 3].
## Source quality
- **Peer-Reviewed Publications**:
- **Specialist Research & Preprints**: [4 preprint]
## References
- **[1]** [ACL paper](https://aclanthology.org/2022.findings-emnlp.534) — `https://aclanthology.org/2022.findings-emnlp.534`
- **[4 preprint]** [Profiling LoRA](https://arxiv.org/html/2509.12229v1) — `https://arxiv.org/html/2509.12229v1`
"""
    out, changed = declutter_citations(body)
    assert "## Detailed analysis" in out
    assert "### Memory" in out
    assert "**[4 preprint]**" in out
    assert "****" not in out
    assert changed > 0
    assert "[1, 2]" in out


def test_format_reference_list_never_emits_empty_marker():
    refs = format_reference_list(
        [
            {"n": 1, "title": "ACL", "url": "https://aclanthology.org/2022.findings-emnlp.534"},
            {"n": None, "title": "Missing n", "url": "https://arxiv.org/abs/2305.14314"},
            {"title": "Also missing", "url": "https://www.nvidia.com/en-us/data-center/a100"},
        ]
    )
    assert "****" not in refs
    assert "**[None]**" not in refs
    assert "**[1]**" in refs
    assert "**[2]**" in refs
    assert "**[3]**" in refs


def test_bind_rebuilds_empty_source_quality_bands():
    md = """# T

Body cites [1] and [2].

## Source quality

- **Peer-Reviewed Publications**: 
- **Specialist Research & Preprints**: 

## References

- old
"""
    citations = [
        {
            "n": 1,
            "title": "ACL",
            "url": "https://aclanthology.org/2022.findings-emnlp.534",
            "tier": "peer_reviewed",
        },
        {
            "n": 2,
            "title": "QLoRA",
            "url": "https://arxiv.org/abs/2305.14314",
            "tier": "specialist_research",
        },
    ]
    out = bind_markdown_to_ledger(md, citations)
    assert "Peer-Reviewed Publications" in out
    assert "1 peer" in out
    assert "Specialist Research" in out
    assert "**[1]**" in out
    assert "**[2]**" in out
    assert "****" not in out
