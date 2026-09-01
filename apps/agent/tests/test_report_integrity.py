from app.domain.report_integrity import audit_memo_integrity, enforce_report_integrity


def test_audit_flags_quant_contradiction():
    memo = (
        "## Quantitative findings\n\n"
        "| Metric | Value |\n|---|---|\n| Measurements | absent in source texts |\n\n"
        "## Decision rule\n\n"
        "### Empirical cutoffs\n"
        "- Use gated RAG when context exceeds 80% saturation [1][6].\n\n"
        "## Worked example\n\n"
        "System prompt uses 1,200 tokens; debug log compresses 4,000 to 180 tokens.\n"
    )
    notes = audit_memo_integrity(memo)
    blob = " ".join(notes).lower()
    assert "contradiction" in blob or "decision rule" in blob
    assert "worked example" in blob or "1,200" in blob


def test_enforce_labels_illustrative_worked_example():
    memo = (
        "## Quantitative findings\n\nMeasurements absent in source texts.\n\n"
        "## Worked example\n\n"
        "The agent loads 5,700 tokens into working memory.\n"
    )
    out = enforce_report_integrity(
        body_markdown=memo,
        executive_summary="Long analytical summary about memory architecture and benchmarks.",
        decision_rule="- Prefer gated routing when uncertain.",
        at_a_glance="",
        citations=[{"n": 1, "url": "https://example.com", "title": "t"}],
        critic={"depth_score": {"score": 68, "label": "standard"}},
        limitations=[],
    )
    assert "Illustrative scenario" in out["body_markdown"]
    assert out["at_a_glance"]
    assert out["at_a_glance"] != memo[:40]


def test_enforce_strips_unresolved_cites():
    memo = "Claim with broken cite [?] [6]."
    out = enforce_report_integrity(
        body_markdown=memo,
        executive_summary="Summary.",
        decision_rule="Rule.",
        at_a_glance="Glance.",
        citations=[],
        critic={},
        limitations=[],
    )
    assert "[?]" not in out["body_markdown"]
    assert any("could not be resolved" in x.lower() for x in out["limitations"])


def test_enforce_regenerates_duplicate_glance():
    dup = "Same text about agents and memory with identical wording throughout."
    out = enforce_report_integrity(
        body_markdown=f"## Executive summary\n\n{dup}\n",
        executive_summary=dup,
        decision_rule="- Start with hybrid retrieval when latency budget is tight.",
        at_a_glance=dup,
        citations=[],
        critic={"depth_score": {"score": 55, "label": "shallow"}},
        limitations=[],
    )
    assert out["at_a_glance"] != dup
    assert "regenerated_at_a_glance" in out["integrity_flags"]


def test_audit_citation_stacking():
    memo = "Benchmarks include SWE-bench and GAIA [3][12][5][8]."
    notes = audit_memo_integrity(memo)
    assert any("stack" in n.lower() for n in notes)
