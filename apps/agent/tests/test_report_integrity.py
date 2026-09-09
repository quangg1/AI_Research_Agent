from app.domain.report_integrity import (
    audit_memo_integrity,
    enforce_report_integrity,
    _insert_illustrative_prefix,
    _worked_example_mostly_ungrounded,
)


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


def test_enforce_strips_citation_on_framework_description_not_in_cited_source():
    """Real memo output described LangGraph's state-graph/checkpointer API and
    AutoGen's group-chat model, each with a citation attached — but grepping
    the actual retrieved evidence text showed neither LangGraph nor AutoGen
    was ever mentioned in any of the 13 sources. The writer filled the
    description in from its own training data and attached a plausible-
    looking [n] rather than what it actually retrieved."""
    query = "Compare OpenAI Agents, LangGraph, and AutoGen for multi-agent orchestration."
    memo = (
        "## Differences between the named options\n\n"
        "- **LangGraph**: LangGraph structures agent workflows as explicit state "
        "graphs with checkpointers for persistence [1].\n"
        "- **AutoGen**: AutoGen provides RoundRobinGroupChat for conversational "
        "multi-agent coordination [2].\n"
    )
    citations = [
        {"n": 1, "url": "https://arxiv.org/abs/1", "title": "Multi-agent survey"},
        {"n": 2, "url": "https://arxiv.org/abs/2", "title": "AutoGen: Enabling Next-Gen LLM Applications"},
    ]
    evidence = [
        {
            "url": "https://arxiv.org/abs/1",
            "full_text": "This survey reviews multi-agent LLM systems, communication protocols, and coordination patterns broadly.",
        },
        {
            "url": "https://arxiv.org/abs/2",
            "full_text": "AutoGen is a framework enabling multi-agent conversation via RoundRobinGroupChat and other group chat patterns.",
        },
    ]
    out = enforce_report_integrity(
        body_markdown=memo,
        executive_summary="Comparison of agent orchestration frameworks.",
        decision_rule="- Prefer graph-based orchestration when state control matters.",
        at_a_glance="",
        citations=citations,
        critic={"depth_score": {"score": 68, "label": "standard"}},
        limitations=[],
        evidence=evidence,
        query=query,
    )
    lines = out["body_markdown"].splitlines()
    langgraph_line = next(l for l in lines if "LangGraph" in l)
    autogen_line = next(l for l in lines if "RoundRobinGroupChat" in l)
    # LangGraph is never mentioned in source [1] -> its citation must be stripped.
    assert "[1]" not in langgraph_line
    # AutoGen genuinely IS discussed in source [2] -> its citation must survive.
    assert "[2]" in autogen_line
    assert "entity_citation_misattributed_stripped" in out["integrity_flags"]


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


def test_enforce_drops_decision_rule_number_misattributed_to_wrong_source():
    """Regression: real memo output cited "76% lower-environment incident
    rate [9 specialist]" in Decision rule, but that 76% figure actually came
    from an uncited vendor blog — source 9's text never mentions it. The old
    sanitizer only dropped numbers with NO citation at all, trusting any [n]
    as proof, so a confidently-but-wrongly-cited number sailed through."""
    memo = "## Quantitative findings\n\nMeasurements absent in source texts.\n\n"
    decision_rule = (
        "- Implement differential privacy to mitigate the baseline 76% "
        "lower-environment incident rate [9 specialist].\n"
        "- Retain real seed data across recursive generations [8 specialist]."
    )
    citations = [
        {"n": 8, "url": "https://arxiv.org/abs/1", "title": "Model collapse paper"},
        {"n": 9, "url": "https://arxiv.org/abs/2", "title": "Best practices paper"},
    ]
    evidence = [
        {
            "url": "https://arxiv.org/abs/1",
            "full_text": "Accumulating real seed data across recursive generations prevents collapse.",
        },
        {
            "url": "https://arxiv.org/abs/2",
            "full_text": "Synthetic data can be generated at scale for training and testing AI models.",
        },
    ]
    out = enforce_report_integrity(
        body_markdown=memo,
        executive_summary="Analytical summary about synthetic data and model collapse.",
        decision_rule=decision_rule,
        at_a_glance="",
        citations=citations,
        critic={"depth_score": {"score": 68, "label": "standard"}},
        limitations=[],
        evidence=evidence,
    )
    assert "76%" not in out["decision_rule"]
    assert "Retain real seed data" in out["decision_rule"]


def test_enforce_renumbers_decision_rule_after_dropping_a_bullet():
    """Regression: dropping an ungrounded numbered bullet left the list
    reading "1. ... 3. ... 4." (real memo output) — the gate worked, but the
    visible gap advertises that content vanished. Renumber sequentially."""
    memo = "## Quantitative findings\n\nMeasurements absent in source texts.\n\n"
    decision_rule = (
        "1. **First**: Retain real seed data across recursive generations [8 specialist].\n"
        "2. **Second**: Mitigate the baseline 76% lower-environment incident rate [9 specialist].\n"
        "3. **Third**: Avoid multi-agent debate on non-medical reasoning tasks [8 specialist]."
    )
    citations = [
        {"n": 8, "url": "https://arxiv.org/abs/1", "title": "Model collapse paper"},
        {"n": 9, "url": "https://arxiv.org/abs/2", "title": "Best practices paper"},
    ]
    evidence = [
        {
            "url": "https://arxiv.org/abs/1",
            "full_text": (
                "Accumulating real seed data across recursive generations prevents collapse. "
                "Avoid multi-agent debate on non-medical reasoning tasks."
            ),
        },
        {
            "url": "https://arxiv.org/abs/2",
            "full_text": "Synthetic data can be generated at scale for training and testing AI models.",
        },
    ]
    out = enforce_report_integrity(
        body_markdown=memo,
        executive_summary="Analytical summary about synthetic data and model collapse.",
        decision_rule=decision_rule,
        at_a_glance="",
        citations=citations,
        critic={"depth_score": {"score": 68, "label": "standard"}},
        limitations=[],
        evidence=evidence,
    )
    lines = [l for l in out["decision_rule"].splitlines() if l.strip()]
    assert [l.split(".")[0] for l in lines] == ["1", "2"]
    assert "First" in lines[0] and "Third" in lines[1]


def test_illustrative_prefix_strips_all_citation_markers():
    """Regression: a real memo's "illustrative" Worked Example still carried
    [3]/[3 specialist] on BOTH genuinely-cited real figures (74.1s, $0.430)
    and invented ones (57.2% baseline) in the same passage — a reader can't
    tell which numbers the disclaimer actually applies to, even though a
    disclaimer sits right above it. Once a section is labeled illustrative,
    no citation marker should survive in it at all — real or not."""
    body = (
        "## Worked example\n\n"
        "The reflexive pipeline reaches a document accuracy of 57.2% [3 specialist], "
        "at a cost of $0.430 and latency of 74.1 seconds [3 specialist].\n\n"
        "## Contradictions & debates\n\nOther text.\n"
    )
    out = _insert_illustrative_prefix(body, "Worked example")
    worked = out.split("## Worked example", 1)[1].split("## Contradictions", 1)[0]
    assert "Illustrative scenario" in worked
    assert "[3" not in worked
    assert "57.2%" in worked  # text stays — only the false citation crutch is removed
    assert "74.1 seconds" in worked
    assert "Other text." in out  # sections outside Worked example untouched


def test_worked_example_ungrounded_catches_two_digit_percentage():
    """Regression: LOAD_NUMBER_RE alone skips bare numbers under 3 digits
    (built for token-count claims like "5,700 tokens"), so a fabricated
    two-digit percentage in Worked Example ("achieves 76% accuracy [1]")
    was invisible to this check even though it's exactly the kind of
    confident-but-invented figure the check exists to catch."""
    section = "The system achieves 76% accuracy on the benchmark [1].\nA second unrelated claim of 82% appears too [1]."
    citations = [{"n": 1, "url": "https://arxiv.org/abs/1", "title": "Paper"}]
    evidence = [{"url": "https://arxiv.org/abs/1", "full_text": "This paper discusses training methodology only."}]
    assert _worked_example_mostly_ungrounded(section, citations=citations, evidence=evidence)


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
