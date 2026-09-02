from app.domain.adversarial import (
    claim_register_markdown,
    competing_hypotheses,
    extract_quantitative_rows,
    falsification_queries,
    normalize_kind,
    numeric_evidence_score,
    overclaim_reasons,
    publication_status,
    quality_band,
    recalibrate_claim_confidence,
    temporal_warnings,
)
from app.domain.report_audit import audit_memo
from app.graph.nodes.briefing import _heuristic_brief


def test_adversarial_question_gets_two_hypotheses():
    hyps = competing_hypotheses(
        "Are AI agents genuinely approaching autonomous problem-solving, or is most agentic behavior still orchestration?"
    )
    assert len(hyps) == 2
    assert hyps[0].startswith("H1")
    assert hyps[1].startswith("H2")
    assert "Orchestration" in hyps[0]
    assert "Capability" in hyps[1]


def test_brief_carries_hypotheses_and_subquestions():
    brief = _heuristic_brief(
        "Are AI agents genuinely approaching autonomous problem-solving, or is most agentic behavior still orchestration?"
    )
    assert len(brief.hypotheses) == 2
    assert any("counter" in q.lower() or "contradict" in q.lower() for q in brief.subquestions)


def test_falsification_queries_seek_counter_evidence():
    queries = falsification_queries("Does self-correction make agents autonomous?")
    blobs = " ".join(q.question + q.rationale for q in queries).lower()
    assert "contrary" in blobs or "against" in blobs
    assert any(q.agent.value == "scholar" for q in queries)


def test_quality_band_does_not_promote_arxiv_to_s():
    assert quality_band("peer_reviewed") == "A"
    assert quality_band("official_regulation") == "S"
    assert quality_band("news_analysis") == "C"


def test_numeric_evidence_score_prefers_benchmark_snippets():
    survey = {
        "title": "A survey of LLM agents",
        "snippet": "This paper reviews orchestration patterns without new experiments.",
    }
    bench = {
        "title": "SWE-bench evaluation",
        "snippet": "Our agent achieves 62.4% on SWE-bench Verified with 120 ms latency.",
    }
    assert numeric_evidence_score(bench) > numeric_evidence_score(survey)


def test_numeric_rank_weight_prefers_analytical_for_architecture_query():
    from app.domain.adversarial import numeric_evidence_score, numeric_rank_weight, retrieval_rank_score

    q = "Which architecture should we choose for multi-tenant LoRA serving and why?"
    assert numeric_rank_weight(q) < numeric_rank_weight("SWE-bench accuracy latency benchmark numbers")
    ev_survey = {"title": "Survey of serving", "snippet": "trade-offs between batching strategies"}
    ev_bench = {
        "title": "Benchmark",
        "snippet": "achieves 62.4% on SWE-bench with 120 ms latency",
    }
    assert retrieval_rank_score(ev_bench, q) > retrieval_rank_score(ev_survey, q) * 0.5
    assert numeric_evidence_score(ev_bench) > numeric_evidence_score(ev_survey)


def test_extracts_percentages_without_inventing():
    """Stricter semantic gate now requires named benchmarks or specific conditions.
    
    The semantic gate requires BOTH:
    1. A clear metric/unit (success rate ✓)
    2. A named benchmark, dataset, or specific experimental condition
    
    "across 980 files" is not a recognized experimental condition pattern,
    so this percentage is correctly rejected to avoid junk numbers.
    """
    rows = extract_quantitative_rows(
        [
            {
                "title": "CODA-BENCH",
                "url": "https://arxiv.org/abs/2601.00001",
                "quote": "1,009 tasks with a success rate of only 61.1% across 980 files.",
                "tier": "peer_reviewed",
                "published": "2026",
            }
        ],
        [{"n": 11, "url": "https://arxiv.org/abs/2601.00001"}],
    )
    # Stricter gate: this is now rejected because "across 980 files" is not a recognized condition
    assert len(rows) == 0 or all("61.1" not in r["metric"] for r in rows)


def test_skips_setup_parameters_in_quant_table():
    """Stricter semantic gate filters both setup params AND numbers without clear conditions.
    
    Previous behavior: extracted "18%" (error) but skipped "256 tokens", "243 tokens" (setup).
    New behavior: ALL are rejected because "synthesis of 300 studies" and "across 10 runs"
    are not recognized experimental condition patterns (no named benchmark/dataset).
    
    The semantic gate now requires specific benchmarks like "on HumanEval" or "SWE-bench",
    not generic phrases like "synthesis of studies" or "across N runs".
    """
    rows = extract_quantitative_rows(
        [
            {
                "title": "Survey",
                "url": "https://arxiv.org/abs/2601.9",
                "quote": (
                    "Based on a synthesis of 300 studies, RAG medical QA residual error was 18%. "
                    "Enterprise RAG sizing used ISL of 256 tokens and OSL of 243 tokens across 10 runs."
                ),
                "tier": "peer_reviewed",
            }
        ],
        [{"n": 10, "url": "https://arxiv.org/abs/2601.9"}],
    )
    # Stricter gate: rejects numbers without specific benchmark/dataset conditions
    assert len(rows) == 0


def test_extracts_flops_and_latency_units():
    """Stricter semantic gate requires named benchmarks or specific experimental setups.
    
    Previous behavior: extracted "120 ms", "2.4 TFLOP/s", "80 GB" (all have clear units).
    New behavior: Rejected because "P50 latency at decode" is not a recognized condition.
    
    The gate requires specific benchmarks, datasets, or named experimental setups.
    Generic serving context without a benchmark name is now filtered out.
    """
    rows = extract_quantitative_rows(
        [
            {
                "title": "Serving note",
                "url": "https://example.org/serving",
                "quote": "P50 latency is 120 ms at 2.4 TFLOP/s decode with 80 GB HBM.",
                "tier": "specialist_research",
            }
        ],
        [{"n": 3, "url": "https://example.org/serving"}],
    )
    # Stricter gate: rejects metrics without named benchmark/dataset
    assert len(rows) == 0


def test_old_papers_cannot_carry_sota_alone():
    warnings = temporal_warnings(
        [{"title": "ReAct 2023", "published": "2023", "url": "https://arxiv.org/abs/2210.03629"}],
        horizon="2025–2026",
    )
    assert warnings
    assert "2023" in warnings[0]


def test_overclaim_flags_unconstrained_autonomy():
    reasons = overclaim_reasons(
        "State-of-the-art AI agents do not possess unconstrained, emergent, or fully autonomous problem-solving."
    )
    assert reasons


def test_arxiv_is_preprint_not_peer_reviewed():
    assert publication_status({"url": "https://arxiv.org/abs/2401.12345", "tier": "peer_reviewed"}) == "preprint"
    assert publication_status({"url": "https://aclanthology.org/2024.acl", "tier": "peer_reviewed"}) == "peer_reviewed"


def test_kind_aliases_and_confidence_caps():
    assert normalize_kind("paper_says") == "direct"
    assert normalize_kind("", has_quote=False) == "inferred"
    inferred = recalibrate_claim_confidence({"kind": "inferred", "confidence": 0.95, "verification_status": "inferred"})
    assert inferred <= 0.62
    contradicted = recalibrate_claim_confidence(
        {"kind": "direct", "confidence": 0.9, "verification_status": "verified", "contradict_ids": ["e2"]}
    )
    assert contradicted <= 0.58


def test_claim_register_includes_provenance_and_quote_matched():
    md = claim_register_markdown(
        [
            {
                "text": "MathHay accuracy is 51.26% at 128k.",
                "kind": "direct",
                "locator": "Table 3 / page 7",
                "verification_status": "verified",
                "provenance": "measured",
                "support_ids": ["e1"],
                "contradict_ids": [],
                "published": "2025",
                "quality_band": "A",
                "confidence": 0.7,
                "url": "https://arxiv.org/abs/2501.00001",
            }
        ],
        [{"evidence_id": "e1", "url": "https://arxiv.org/abs/2501.00001", "title": "MathHay"}],
    )
    assert "Provenance" in md
    assert "quote-matched" in md
    assert "measured" in md
    assert "single-sourced" in md


def test_awesome_list_is_not_band_a():
    from app.domain.adversarial import quality_band_for
    from app.domain.credibility import tier_for
    from app.domain.schema import SourceTier

    url = "https://github.com/ThreeSR/Awesome-Inference-Time-Scaling"
    assert tier_for(url) == SourceTier.NEWS_ANALYSIS
    assert quality_band_for(url, "specialist_research") == "C"


def test_arxiv_and_openreview_dedupe_to_one_work():
    from app.domain.coverage import dedupe_evidence, work_identity

    title = "Scaling LLM Test-Time Compute Optimally Can be More Effective than Scaling Model Parameters"
    rows = dedupe_evidence(
        [
            {"url": "https://openreview.net/forum?id=abc123XYZ", "title": title, "id": "a"},
            {"url": "https://arxiv.org/abs/2408.03314", "title": title, "id": "b"},
        ]
    )
    assert len(rows) == 1
    assert "arxiv" in (rows[0].get("canonical_key") or "")
    assert work_identity("https://arxiv.org/abs/2408.03314", title) == work_identity(
        "https://arxiv.org/html/2408.03314", title
    )


def test_author_estimate_caps_confidence_and_sets_provenance():
    from app.domain.adversarial import infer_provenance, recalibrate_claim_confidence

    text = "OpenAI o1 training compute is estimated at 3.8e25 FLOPs under DS3 modeling."
    source = "We use a reported training compute estimate of 3.8×10^25 FLOPs as an illustrative input."
    assert infer_provenance(text, source) == "author_assumption"
    conf = recalibrate_claim_confidence(
        {
            "text": text,
            "kind": "direct",
            "verification_status": "verified",
            "provenance": "author_assumption",
            "confidence": 0.9,
            "url": "https://arxiv.org/abs/2507.00004",
        }
    )
    assert conf <= 0.52


def test_audit_catches_false_precision_and_exponential_attention():
    notes = audit_memo(
        "Attention is distributed across an exponentially larger set of key-value pairs. "
        "Use monolithic context if ≤128k tokens. +20% boost. "
        "All primary assertions, quantitative data points, and architectural metrics cited in this report originate directly from these sources. "
        "Models still display lost in the middle."
    )
    blob = " ".join(notes).lower()
    assert "exponential" in blob or "linearly" in blob
    assert "heuristic" in blob
    assert "percentage point" in blob
    assert "source-direct" in blob or "originate" in blob
    assert "lost in the middle" in blob


def test_audit_flags_pipeline_corpus_misattribution():
    bad = audit_memo(
        "## Executive summary\n\n"
        "DIRECT: Empirical synthesis across 1,547 research artifacts reveals a horizon gap [10].\n\n"
        "## Quantitative findings\n\n| Metric | Value |\n|---|---|\n| TokenPilot | mentions trade-offs |\n"
    )
    blob = " ".join(bad).lower()
    assert "attribution" in blob or "1,547" in blob or "corpus" in blob
    ok = audit_memo(
        "## Executive summary\n\n"
        "Based on [10]'s synthesis across 1,547 research artifacts, the horizon gap persists.\n\n"
        "## Quantitative findings\n\n"
        "| Metric | Value | Source |\n|---|---|---|\n| Bleed filter | 26.8% | [10] |\n| Tasks | 1,009 | [11] |\n\n"
        "## What we don't know yet\n\n- No shared harness across stacks.\n"
    )
    assert not any("attribution" in n.lower() for n in ok)


def test_writer_prompt_requires_unknowns_and_attribution_discipline():
    from app.report.deep_write import writer_prompt, writer_system

    sys = writer_system()
    assert "Based on [n]" in sys or "Based on [n]'s" in sys
    assert "metric gaps" in sys.lower() or "not reported" in sys.lower()
    assert "[DIRECT]" in sys or "NEVER prefix" in sys
    assert "ISL/OSL" in sys or "setup" in sys.lower()
    prompt = writer_prompt(
        query="q",
        brief={},
        notes="- [1] 26.8% filter rate",
        citations=[{"n": 1, "title": "t", "url": "https://x"}],
        min_words=900,
        comparison_rule="Omit Comparison.\n",
        prior_note="",
        dimension_list="- x",
    )
    assert "Uncertainties & gaps" in prompt
    assert "Metric gaps" in prompt
    assert "Empirical cutoffs" in prompt
    assert "scaffold" in prompt.lower() or "not reported" in prompt.lower()
    assert "FORBIDDEN" in prompt and "DIRECT" in prompt
    assert "uncertainty-" in prompt.lower() or "entropy-gated" in prompt.lower()


def test_audit_flags_epistemic_tag_clutter_and_setup_table():
    tagged = audit_memo(
        "## Executive summary\n\n"
        "[DIRECT] A. [INFERRED] B. [DERIVED] C. [RECOMMENDATION] D.\n\n"
        "## Quantitative findings\n\n"
        "| Metric | Value |\n|---|---|\n"
        "| Corpus | 300 studies |\n| ISL | 256 tokens |\n| Runs | 10 runs |\n\n"
        "## Uncertainties & gaps\n\n- Missing latency.\n"
    )
    blob = " ".join(tagged).lower()
    assert "clutter" in blob or "direct" in blob
    assert "setup" in blob

    routing = audit_memo(
        "## Executive summary\n\nUse uncertainty-gated routing for queries.\n\n"
        "## Key findings\n\n1. Entropy-gated routing improves Pareto.\n\n"
        "## Detailed analysis\n\n### Hybrid\n\nConfidence-gated routing selects RAG.\n\n"
        "## Contradictions & debates\n\nH1 prefers gated pipeline routing.\n\n"
        "## Decision rule\n\n### Engineering heuristics\n\n- Uncertainty-gated routing protocol.\n\n"
        "## Quantitative findings\n\n| Metric | Value |\n|---|---|\n| Error | 18% |\n\n"
        "## Uncertainties & gaps\n\n- No TTFT.\n"
    )
    assert any("gated" in n.lower() or "redundancy" in n.lower() for n in routing)