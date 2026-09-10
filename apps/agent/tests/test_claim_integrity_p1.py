from app.domain.citations import annotate_inline_citation_tiers, inline_tier_label
from app.domain.claim_quote_verify import enrich_claim_quotes_llm
from app.domain.report_integrity import (
    build_integrity_reloop_followups,
    integrity_severity,
)
from app.graph.builder import after_report


def test_inline_tier_label_peer_and_repo():
    # arXiv links are never labeled "peer" even when tier says peer_reviewed
    # — the cited version itself isn't provably the reviewed one (e506a8b,
    # "demote arXiv/predatory venues from peer Band A", from the LoRA memo
    # audit's overclaiming findings).
    assert inline_tier_label({"tier": "peer_reviewed", "url": "https://arxiv.org/abs/2401.1"}) == "preprint"
    assert inline_tier_label({"tier": "specialist_research", "url": "https://github.com/org/repo"}) == "repo"


def test_annotate_inline_citation_tiers():
    citations = [
        {"n": 3, "tier": "peer_reviewed", "url": "https://arxiv.org/abs/2401.1"},
        {"n": 12, "tier": "specialist_research", "url": "https://github.com/x/y"},
    ]
    md = "Benchmarks improve latency [3] and tooling [12]."
    out = annotate_inline_citation_tiers(md, citations)
    assert "[3 preprint]" in out
    assert "[12 repo]" in out


def test_annotate_skips_already_tagged():
    citations = [{"n": 3, "tier": "peer_reviewed", "url": "https://arxiv.org/abs/2401.1"}]
    md = "Already tagged [3 peer]."
    assert annotate_inline_citation_tiers(md, citations) == md


def test_annotate_skips_latex_interval_that_looks_like_citation_list():
    """Regression: "$c \\in [0, 1]$" has the same bracket shape as a citation
    list. The old code annotated it as citing sources 0 and 1, producing
    "$c \\in [0, 1 peer]$" inside a live LaTeX formula (real memo output,
    2026-09-05 run)."""
    citations = [{"n": 1, "tier": "peer_reviewed", "url": "https://arxiv.org/abs/2401.1"}]
    md = r"confidence values $c \in [0, 1]$ across subgroup bins [1]."
    out = annotate_inline_citation_tiers(md, citations)
    assert r"$c \in [0, 1]$" in out
    assert "[1 preprint]" in out


def test_integrity_severity_critical():
    memo = (
        "## Quantitative findings\n\nMeasurements absent in source texts.\n\n"
        "## Worked example\n\nLoads 5,700 tokens without cite.\n"
    )
    issues = [
        "Internal contradiction: Quantitative findings admits no measured data, "
        "but Worked example cites specific figures (5,700) without source backing."
    ]
    assert integrity_severity(issues, memo) == "critical"
    assert integrity_severity([], memo) == "ok"


def test_build_integrity_reloop_followups():
    followups = build_integrity_reloop_followups("Kiln memory architecture", ["contradiction"])
    assert len(followups) == 2
    assert followups[0]["agent"] == "search"
    assert followups[1]["agent"] == "scholar"
    assert "Measured benchmarks" in followups[0]["question"]


def test_after_report_routes_integrity_research():
    assert after_report({"status": "integrity_research"}) == "planner"
    assert after_report({"status": "draft"}) == "memo_gate"


def test_enrich_claim_quotes_heuristic():
    evidence = [
        {
            "url": "https://example.com/paper",
            "full_text": "The model achieves 92% accuracy on the benchmark suite under standard conditions.",
            "title": "Paper",
        }
    ]
    claims = [
        {
            "id": "C1",
            "text": "Model achieves 92% accuracy on the benchmark suite.",
            "url": "https://example.com/paper",
            "quote": "",
        }
    ]
    out, stats = enrich_claim_quotes_llm(claims, evidence, [])
    assert stats["heuristic"] == 1
    assert "92%" in out[0]["quote"]
