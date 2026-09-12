from app.domain.report_integrity import (
    audit_memo_integrity,
    enforce_report_integrity,
    _citation_stacks,
    _insert_illustrative_prefix,
    _load_bearing_source_concentration,
    _reader_facing_limitations,
    _worked_example_mostly_ungrounded,
)


def test_audit_does_not_rescan_its_own_limitations_bullets():
    """Regression: audit_body_numbers()/_citation_stacks() scanned the WHOLE
    body including ## Limitations. Once one report-regeneration pass folded
    a finding like "Unverified attribution: 7X cited to [1], [2] ..." into
    Limitations, the NEXT pass's audit re-discovered "7X"/"[1]"/"[2]" inside
    that diagnostic sentence as if it were a fresh prose claim, compounding
    into ever-longer, garbled bullets ("cited to [8], [1],,,,") across
    retries on a real multi-regeneration run."""
    citations = [{"n": 1, "url": "https://arxiv.org/abs/1"}]
    evidence = [{"url": "https://arxiv.org/abs/1", "full_text": "No numbers here at all."}]
    memo = (
        "## Executive summary\n\nA grounded claim with no figures.\n\n"
        "## Limitations\n\n"
        "- Unverified attribution: 7X cited to [1], [2], [8], [3], but the cited source "
        "text does not contain the figure — treat as unsourced until re-checked.\n"
    )
    notes = audit_memo_integrity(memo, citations=citations, evidence=evidence)
    assert not any("7X" in n or "7x" in n.lower() for n in notes)


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


def test_audit_flags_low_tier_quantitative_backbone():
    """A high confidence score is misleading when the actual cited numbers
    trace mostly to unknown/vendor-tier sources (real run: VRAM/GLUE figures
    cited almost entirely to a marketing blog + a LinkedIn post, both tagged
    'unknown' in Source quality, while the confidence card said "Primary
    sources 100%")."""
    memo = (
        "## Key findings\n\n"
        "1. Peak VRAM reduction 89% [4].\n"
        "2. GLUE accuracy 89.5% [5].\n\n"
        "## Quantitative findings\n\n"
        "| Metric | Value | Source |\n|---|---|---|\n| VRAM | 89% | [4] |\n"
    )
    citations = [
        {"n": 4, "url": "https://blockchain-council.org/x", "tier": "unknown"},
        {"n": 5, "url": "https://linkedin.com/x", "tier": ""},
    ]
    notes = audit_memo_integrity(memo, citations=citations)
    assert any("Band C" in n for n in notes)


def test_audit_allows_balanced_quantitative_backbone():
    memo = (
        "## Key findings\n\n"
        "1. Peak VRAM reduction 89% [4].\n"
        "2. GLUE accuracy 89.5% [1].\n"
    )
    citations = [
        {"n": 4, "url": "https://blockchain-council.org/x", "tier": "unknown"},
        {"n": 1, "url": "https://arxiv.org/abs/2106.09685", "tier": "peer_reviewed"},
    ]
    notes = audit_memo_integrity(memo, citations=citations)
    assert not any("Band C" in n for n in notes)


def test_enforce_strips_disclaimer_misplaced_after_real_cutoffs():
    """The writer prompt hands the model a quotable 'Evidence-backed
    threshold: none.' fallback for when nothing was measured — real run
    showed it tacked on after Engineering heuristics even though Empirical
    cutoffs already listed 2 real cited thresholds above it."""
    body = (
        "## Quantitative findings\n\n| Metric | Value |\n|---|---|\n| VRAM reduction | 75% [1] |\n\n"
        "## Decision rule\n\n"
        "### Empirical cutoffs (sources only)\n\n"
        "- VRAM Threshold: <=12 GB requires QLoRA [1 preprint].\n"
        "- Compute Capability: requires > 7.5 [4 primary].\n\n"
        "### Engineering heuristics (AI suggestion — not from papers)\n\n"
        "- Target all linear layers, not just query/value.\n\n"
        "Evidence-backed threshold: none in collected sources — use Engineering heuristics "
        "below for design guidance only.\n\n"
        "## Uncertainties & gaps\n\nSomething.\n"
    )
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="Summary.",
        decision_rule="- Prefer QLoRA under 12GB.",
        at_a_glance="",
        citations=[{"n": 1, "url": "https://arxiv.org/abs/1"}],
        critic={"depth_score": {"score": 80, "label": "deep"}},
        limitations=[],
    )
    assert "evidence-backed threshold" not in out["body_markdown"].lower()
    assert "VRAM Threshold" in out["body_markdown"]


def test_enforce_surfaces_unverified_number_attribution_to_the_reader():
    """Real run: the audit found "100x cited to [1] but not found in those
    sources" (plus 1x/3x/16x) and stored it in metrics, while the memo kept
    printing "up to 100x ... [1]" unhedged and the reader-facing Limitations
    section said nothing about it. The writer never sees the post-write
    audit, so the finding has to be folded in here."""
    body = (
        "## Executive summary\n\nCompute scales up to 100x over baseline [1].\n\n"
        "## Limitations\n\n"
        "This evaluation is bounded by the published benchmarks available in the\n"
        "gathered source set [1]. Framework mechanics rely on official platform\n"
        "documentation rather than independent head-to-head measurement.\n"
    )
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="Compute scales up to 100x over baseline [1].",
        decision_rule="",
        at_a_glance="",
        citations=[{"n": 1, "url": "https://example.org/survey"}],
        critic={"depth_score": {"score": 70, "label": "standard"}},
        limitations=[
            "This analysis uses 8 sources across 6 hosts — not a complete review of the literature.",
            "100x cited to [1] but not found in those sources",
            "16x cited to [1] but not found in those sources",
            "These critical dimensions remain weak or unverified: Direct answer to the question as asked.",
        ],
    )
    limits = _section_text(out["body_markdown"], "Limitations")
    assert "unverified attribution" in limits.lower()
    assert "100x" in limits and "16x" in limits
    assert limits.lower().count("unverified attribution") == 1  # grouped, not one line per number
    assert "remain weak or unverified" in limits
    # Process boilerplate stays out of the reader's way.
    assert "not a complete review of the literature" not in limits
    # The writer's own prose is kept, not replaced.
    assert "bounded by the published benchmarks" in limits


def test_enforce_tells_the_reader_which_named_subject_has_no_evidence():
    """Real run: the question named five agent frameworks, the memo compared
    four, and "Anthropic" was flagged only inside critic.reasons — the reader
    got a four-way comparison with nothing saying the fifth was never
    covered."""
    body = (
        "## Executive summary\n\nFour frameworks are compared here [1].\n\n"
        "## Limitations\n\n"
        "This evaluation is bounded by the published benchmarks available in the\n"
        "gathered source set [1]. Framework mechanics rely on official platform\n"
        "documentation rather than independent head-to-head measurement.\n"
    )
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="Four frameworks are compared here [1].",
        decision_rule="",
        at_a_glance="",
        citations=[{"n": 1, "url": "https://example.org/survey"}],
        critic={
            "depth_score": {"score": 70, "label": "standard"},
            "reasons": ["Named subjects with no dedicated evidence: Anthropic, Claude-based"],
        },
        limitations=[],
    )
    limits = _section_text(out["body_markdown"], "Limitations")
    assert "Anthropic" in limits
    assert "never covered by its own evidence" in limits.lower()


def _section_text(markdown: str, heading: str) -> str:
    import re as _re

    match = _re.search(rf"^##\s+{heading}\s*$", markdown, _re.I | _re.M)
    assert match, f"missing ## {heading}"
    rest = markdown[match.end() :]
    nxt = _re.search(r"^##\s+", rest, _re.M)
    return (rest[: nxt.start()] if nxt else rest).strip()


def test_enforce_relocates_disclaimer_into_empty_empirical_cutoffs():
    """Real run: Empirical cutoffs' only bullet had a threshold with no [n]
    citation, so the uncited-number sanitizer stripped it, leaving "Act on
    these" with nothing under it -- while a stale "no evidence-backed
    threshold" line the writer had put after Engineering heuristics stayed
    there, dangling a "use Engineering heuristics below" pointer at nothing
    (the section it points at is now above it, not below)."""
    decision_rule = (
        "### Empirical cutoffs (sources only)\n\n"
        "**Act on these — the sources support them directly:**\n\n"
        "- VRAM threshold: <=12 GB requires QLoRA.\n\n"
        "### Engineering heuristics (AI suggestion — not from papers)\n\n"
        "- Target all linear layers, not just query/value.\n\n"
        "Evidence-backed threshold: none in collected sources — use Engineering heuristics "
        "below for design guidance only."
    )
    body = (
        "## Executive summary\n\nSummary text.\n\n"
        "## Decision rule\n\n" + decision_rule + "\n\n"
        "## Uncertainties & gaps\n\nSomething.\n"
    )
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="Summary text.",
        decision_rule=decision_rule,
        at_a_glance="",
        citations=[],
        critic={"depth_score": {"score": 80, "label": "deep"}},
        limitations=[],
    )
    result = out["body_markdown"]
    assert "use engineering heuristics below" not in result.lower()
    assert "act on these" not in result.lower()
    assert result.lower().count("evidence-backed threshold") == 1
    # report.py re-stitches "## Decision rule" from out["decision_rule"]
    # independent of body_markdown right after this call -- if this string
    # wasn't kept in sync with the fixed body, that re-stitch would silently
    # undo the fix (real regression: fixed in report_integrity.py, then
    # overwritten back to the stale text by report.py's own rebuild step).
    assert "use engineering heuristics below" not in out["decision_rule"].lower()
    assert "act on these" not in out["decision_rule"].lower()
    empirical_idx = result.index("Empirical cutoffs")
    engineering_idx = result.index("Engineering heuristics")
    disclaimer_idx = result.lower().index("evidence-backed threshold")
    assert empirical_idx < disclaimer_idx < engineering_idx


def test_enforce_keeps_disclaimer_when_no_real_cutoff_exists():
    body = (
        "## Decision rule\n\n"
        "### Empirical cutoffs (sources only)\n\n"
        "Evidence-backed threshold: none in collected sources.\n\n"
        "### Engineering heuristics (AI suggestion — not from papers)\n\n"
        "- Some qualitative heuristic.\n\n"
        "## Uncertainties & gaps\n\nSomething.\n"
    )
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="Summary.",
        decision_rule=(
            "### Empirical cutoffs (sources only)\n\n"
            "Evidence-backed threshold: none in collected sources.\n\n"
            "### Engineering heuristics (AI suggestion — not from papers)\n\n"
            "- Some qualitative heuristic."
        ),
        at_a_glance="",
        citations=[],
        critic={"depth_score": {"score": 80, "label": "deep"}},
        limitations=[],
    )
    assert "evidence-backed threshold" in out["body_markdown"].lower()


def test_citation_stacks_ignores_source_quality_and_references_bands():
    """Source quality / References deliberately merge every cite for a band
    into one bracket group (RACE criteria) -- that's by design, not stacking."""
    memo = (
        "## Executive summary\n\nOne clean claim [1 preprint].\n\n"
        "## Source quality\n\n"
        "- Official Documentation & Standards: [4][5][6][7 primary]\n\n"
        "## References\n\n"
        "[4] A — url\n[5] B — url\n[6] C — url\n[7] D — url\n"
    )
    assert _citation_stacks(memo) == []


def test_citation_stacks_still_catches_real_prose_stacking():
    memo = (
        "## Executive summary\n\n"
        "A single sentence backed by everything [1][2][3 primary].\n"
    )
    assert _citation_stacks(memo)


def test_load_bearing_source_concentration_flags_unranked_lead_source():
    """Real run: an 'unknown'-tier personal blog ([1]) was the sole citation
    for the Executive summary's opening sentence and most of Key findings,
    while peer/primary sources in the ledger went unused for those claims."""
    citations = [
        {"n": 1, "url": "https://blog.example.com/x", "tier": "unknown"},
        {"n": 2, "url": "https://arxiv.org/abs/1", "tier": "peer_reviewed"},
    ]
    body = (
        "## Executive summary\n\n"
        "Multi-agent systems win on long-horizon tasks [1].\n\n"
        "## Key findings\n\n"
        "1. Claim A holds because of structural isolation [1].\n"
        "2. Claim B follows the same pattern [1].\n"
        "3. Claim C is only weakly related [2 preprint].\n"
    )
    note = _load_bearing_source_concentration(body, citations, "", "")
    assert note
    assert "[1]" in note
    assert "opening claim" in note


def test_load_bearing_source_concentration_counts_detailed_analysis_too():
    """Real run: Executive summary + Key findings alone only had 2 sentences
    sole-cited to the unranked source (below the repetition threshold) --
    the same source was ALSO the sole citation for 3 more sentences spread
    across Detailed analysis subsections, which is where most of a memo's
    actual claims live. Scanning only the short summary sections missed a
    genuine, memo-wide over-reliance on one unverified page."""
    citations = [{"n": 1, "url": "https://blog.example.com/x", "tier": "unknown"}]
    body = (
        "## Executive summary\n\nOne claim here [1]. Another point stands alone [1].\n\n"
        "## Key findings\n\n1. A related but separately-sourced point [2 preprint].\n\n"
        "## Detailed analysis\n\n"
        "### First angle\n\nA claim resting only on this page [1].\n\n"
        "### Second angle\n\nAnother claim resting only on this page [1].\n\n"
        "### Third angle\n\nYet another claim resting only on this page [1].\n"
    )
    note = _load_bearing_source_concentration(body, citations, "", "")
    assert note
    assert "5 statement(s)" in note


def test_load_bearing_source_concentration_ignores_a_well_tiered_source():
    """A source cited alone for several load-bearing claims is not a problem
    when it's a real primary/peer source -- only the unranked-tier case is."""
    citations = [
        {"n": 1, "url": "https://arxiv.org/abs/1", "tier": "peer_reviewed"},
    ]
    body = (
        "## Executive summary\n\nMulti-agent systems win on long-horizon tasks [1].\n\n"
        "## Key findings\n\n1. Claim A [1].\n2. Claim B [1].\n3. Claim C [1].\n"
    )
    assert _load_bearing_source_concentration(body, citations, "", "") == ""


def test_load_bearing_source_concentration_needs_real_repetition():
    """One sole-sourced claim on an unranked reference is normal -- only
    flag when it's carrying the memo's main claims by itself repeatedly."""
    citations = [{"n": 1, "url": "https://blog.example.com/x", "tier": "unknown"}]
    body = (
        "## Executive summary\n\nSomething else drives the finding [1].\n\n"
        "## Key findings\n\n1. Unrelated point, well covered [2 preprint].\n"
    )
    assert _load_bearing_source_concentration(body, citations, "", "") == ""


def test_reader_facing_limitations_dedupes_repeated_citation_in_group():
    """Regression: real memo output showed "cited to [2], [4], [2]" -- the
    same source listed twice in one group because the underlying line cited
    [2] in two separate bracket markers before [4]."""
    limitations = [
        "8x cited to [2], [4], [2] but not found in those sources",
    ]
    out = _reader_facing_limitations(limitations)
    assert len(out) == 1
    assert "[2], [4], [2]" not in out[0]
    assert "[2], [4]" in out[0]


def test_reader_facing_limitations_merges_same_numbers_across_citation_groups():
    """Regression: a paragraph and a comparison table both restated the same
    "8x, 16x, 0.95" figures with slightly different citations attached
    ([2],[4] vs just [4]), producing two near-duplicate reader-facing
    bullets about the exact same unverified numbers."""
    limitations = [
        "8x cited to [2], [4] but not found in those sources",
        "16x cited to [2], [4] but not found in those sources",
        "0.95 cited to [2], [4] but not found in those sources",
        "8x cited to [4] but not found in those sources",
        "16x cited to [4] but not found in those sources",
        "0.95 cited to [4] but not found in those sources",
    ]
    out = _reader_facing_limitations(limitations)
    unverified = [n for n in out if n.startswith("Unverified attribution")]
    assert len(unverified) == 1
    assert "8x, 16x, 0.95" in unverified[0]
    assert "[2]" in unverified[0] and "[4]" in unverified[0]


def test_reader_facing_limitations_dedupes_repeated_citation_stacking_measurements():
    """Regression: audit_memo_integrity() runs at several pipeline stages on
    progressively edited bodies, so a real run produced three near-identical
    "N statement(s) cite 3+ sources" bullets (15, 9, 8) back to back --
    keep only the latest measurement, not one per pipeline pass."""
    limitations = [
        "Citation stacking: 15 sentence(s) attach 3+ sources at once (e.g. [5]). "
        "Prefer 1–2 citations per claim with a supporting quote.",
        "Citation stacking: 9 sentence(s) attach 3+ sources at once (e.g. [2, 9]). "
        "Prefer 1–2 citations per claim with a supporting quote.",
        "Citation stacking: 8 sentence(s) attach 3+ sources at once (e.g. [2, 9]). "
        "Prefer 1–2 citations per claim with a supporting quote.",
    ]
    out = _reader_facing_limitations(limitations)
    stacking = [n for n in out if "cite 3 or more sources" in n]
    assert len(stacking) == 1
    assert "8 statement(s)" in stacking[0]


def test_reader_facing_limitations_rewrites_stacking_and_density_and_keeps_concentration():
    limitations = [
        "Citation stacking: 17 sentence(s) attach 3+ sources at once (e.g. [4]). "
        "Prefer 1–2 citations per claim with a supporting quote.",
        "Information density: the same themes (e.g. sandbox filtering, model collapse) "
        "repeat across many sections without new facts. Cut repetition; add named sources/metrics "
        "from notes instead.",
        "Source concentration: [1] is an unranked/unverified source, yet it is the sole "
        "citation for 5 statement(s) in Executive summary/At a glance/Key findings, including "
        "the Executive summary's opening claim, — no higher-tier source corroborates it independently.",
    ]
    out = _reader_facing_limitations(limitations)
    assert any("17 statement(s)" in n for n in out)
    density = next(n for n in out if "repeat across multiple sections" in n)
    assert "sandbox filtering" not in density  # fake placeholder example must not leak
    assert any(n.startswith("Source concentration:") for n in out)


def test_enforce_report_integrity_surfaces_source_concentration_in_body():
    citations = [
        {"n": 1, "url": "https://blog.example.com/x", "tier": "unknown"},
        {"n": 2, "url": "https://arxiv.org/abs/1", "tier": "peer_reviewed"},
    ]
    body = (
        "## Executive summary\n\nMulti-agent systems win on long-horizon tasks [1].\n\n"
        "## Key findings\n\n"
        "1. Claim A holds because of structural isolation [1].\n"
        "2. Claim B follows the same pattern [1].\n"
        "3. Claim C is only weakly related [2 preprint].\n\n"
        "## Decision rule\n\n### Empirical cutoffs (sources only)\n\n- x [1].\n\n"
        "### Engineering heuristics (AI suggestion — not from papers)\n\n- y.\n\n"
        "## Limitations\n\nSome existing caveat.\n"
    )
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="Multi-agent systems win on long-horizon tasks [1].",
        decision_rule="### Empirical cutoffs (sources only)\n\n- x [1].",
        at_a_glance="",
        citations=citations,
        critic={"depth_score": {"score": 80, "label": "deep"}},
        limitations=[],
    )
    assert "Source concentration:" in out["body_markdown"]


def test_enforce_strips_citation_markers_with_no_ledger_entry():
    """Real run: the writer cited [6]/[7] (planned mandatory sources that
    were never actually fetched into the ledger) four times in the body.
    bind_markdown_to_ledger only rebuilds References from `citations` --
    it never scrubs a bare prose marker with no matching entry, so [6]/[7]
    stayed cited in the body with zero corresponding entry in References,
    leaving the reader unable to look them up at all."""
    citations = [
        {"n": 1, "url": "https://arxiv.org/abs/1", "tier": "peer_reviewed"},
    ]
    body = (
        "## Executive summary\n\nOne grounded claim [1].\n\n"
        "## Key findings\n\n"
        "1. A claim citing a source that was never fetched [6].\n"
        "2. Another claim citing the same missing source [6, 7].\n\n"
        "## References\n\n- **[1]** Real paper — https://arxiv.org/abs/1\n"
    )
    out = enforce_report_integrity(
        body_markdown=body,
        executive_summary="One grounded claim [1].",
        decision_rule="- x [1].",
        at_a_glance="",
        citations=citations,
        critic={"depth_score": {"score": 80, "label": "deep"}},
        limitations=[],
    )
    before_refs = out["body_markdown"].split("## References")[0]
    assert "[6]" not in before_refs
    assert "[7]" not in before_refs
    assert "[1]" in before_refs
    assert "stripped_dangling_citation_numbers" in out["integrity_flags"]
