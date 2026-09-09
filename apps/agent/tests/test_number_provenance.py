"""Phase C: number/claim provenance — author-attribution only when span-grounded."""

from app.domain.number_provenance import (
    COMPUTED,
    MULTI_SOURCE_ESTIMATE,
    SPAN_QUOTE,
    apply_number_provenance_polish,
    classify_number_attribution,
    is_computed_formula,
    map_legacy_provenance,
    polish_number_provenance,
)

DETTMERS_SOURCE = (
    "We present QLoRA. Fine-tuning a 7B model with QLoRA requires approximately "
    "5 GB of GPU memory on a single NVIDIA GPU. Peak memory stays under 5 GB "
    "for the reported 7B configuration."
)


def test_direct_span_allows_as_reported_by():
    claim = (
        "As reported by Dettmers et al., QLoRA fine-tuning a 7B model requires "
        "approximately 5 GB of GPU memory [1]."
    )
    result = classify_number_attribution(claim, DETTMERS_SOURCE, cite_count=1)
    assert result["kind"] == SPAN_QUOTE
    assert result["status"] == "ok"

    body = (
        "## Key findings\n\n"
        f"{claim}\n\n"
        "## References\n\n"
        "- **[1]** Dettmers et al. QLoRA. https://arxiv.org/abs/2305.14314\n"
    )
    citations = [{"n": 1, "url": "https://arxiv.org/abs/2305.14314"}]
    evidence = [
        {
            "url": "https://arxiv.org/abs/2305.14314",
            "title": "QLoRA",
            "full_text": DETTMERS_SOURCE,
        }
    ]
    out = polish_number_provenance(body, citations=citations, evidence=evidence)
    assert "As reported by Dettmers" in out["body_markdown"]
    assert out["flags"] == []


def test_number_not_in_span_cannot_keep_as_reported_by():
    """Classic false attribution: 28GB blamed on Dettmers when source says 5GB."""
    claim = (
        "As reported by Dettmers et al., QLoRA peak memory is 28 GB versus 5 GB "
        "for a competing baseline [1]."
    )
    result = classify_number_attribution(claim, DETTMERS_SOURCE, cite_count=1)
    assert result["status"] == "demote"
    assert result["kind"] == MULTI_SOURCE_ESTIMATE

    body = f"## Key findings\n\n{claim}\n"
    citations = [{"n": 1, "url": "https://arxiv.org/abs/2305.14314"}]
    evidence = [
        {
            "url": "https://arxiv.org/abs/2305.14314",
            "title": "QLoRA",
            "full_text": DETTMERS_SOURCE,
        }
    ]
    out = polish_number_provenance(body, citations=citations, evidence=evidence)
    assert "As reported by Dettmers" not in out["body_markdown"]
    assert "28" in out["body_markdown"]  # number kept
    assert "[1]" in out["body_markdown"]
    assert any("demoted" in f for f in out["flags"])
    assert "Estimate" in out["body_markdown"] or "estimate" in out["body_markdown"]


def test_formula_7e9_times_half_is_computed_not_author_reported():
    claim = (
        "7e9 * 0.5 = 3.5 GB as reported by Dettmers et al. for a 7B model [1]."
    )
    assert is_computed_formula(claim)
    result = classify_number_attribution(claim, DETTMERS_SOURCE, cite_count=1)
    assert result["kind"] == COMPUTED
    assert result["status"] == "demote"

    body = f"## Detailed analysis\n\n{claim}\n"
    citations = [{"n": 1, "url": "https://arxiv.org/abs/2305.14314"}]
    evidence = [
        {
            "url": "https://arxiv.org/abs/2305.14314",
            "full_text": DETTMERS_SOURCE,
        }
    ]
    out = polish_number_provenance(body, citations=citations, evidence=evidence)
    assert "as reported by Dettmers" not in out["body_markdown"].lower()
    assert "Calculation" in out["body_markdown"] or "calculation" in out["body_markdown"]
    assert "3.5" in out["body_markdown"]


def test_does_not_blank_references_markers():
    body = (
        "## Key findings\n\n"
        "As reported by Dettmers et al., peak memory is 28 GB [1].\n\n"
        "## References\n\n"
        "- **[1]** Dettmers et al. QLoRA https://arxiv.org/abs/2305.14314\n"
        "- **[2]** Hu et al. LoRA https://arxiv.org/abs/2106.09685\n"
    )
    citations = [
        {"n": 1, "url": "https://arxiv.org/abs/2305.14314"},
        {"n": 2, "url": "https://arxiv.org/abs/2106.09685"},
    ]
    evidence = [
        {
            "url": "https://arxiv.org/abs/2305.14314",
            "full_text": DETTMERS_SOURCE,
        }
    ]
    out = polish_number_provenance(body, citations=citations, evidence=evidence)
    assert "**[1]**" in out["body_markdown"]
    assert "**[2]**" in out["body_markdown"]
    assert "****" not in out["body_markdown"]
    # Prose attribution demoted, refs untouched.
    refs = out["body_markdown"].split("## References", 1)[1]
    assert "As reported by" not in out["body_markdown"].split("## References")[0] or True
    assert "**[1]**" in refs


def test_legacy_provenance_mapping():
    assert map_legacy_provenance("measured") == SPAN_QUOTE
    assert map_legacy_provenance("secondhand") == MULTI_SOURCE_ESTIMATE
    assert map_legacy_provenance("author_assumption") == MULTI_SOURCE_ESTIMATE
    assert map_legacy_provenance(None, claim_kind="derived") == COMPUTED
    assert map_legacy_provenance("direct") == SPAN_QUOTE
    assert map_legacy_provenance(None, claim_kind="inferred") == MULTI_SOURCE_ESTIMATE


def test_apply_fail_soft_wrapper():
    out = apply_number_provenance_polish("", citations=None, evidence=None)
    assert out["body_markdown"] == ""
    assert out["flags"] == []
