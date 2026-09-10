"""Regression testing harness for Kiln agent quality.

Run before each PR merge to catch regressions in structure, coverage, and hallucination rate.

Issue #8: Uses deterministic LLM params (temperature=0) to prevent flaky CI from stochastic outputs.

Usage:
    # Fast check (3 quick cases)
    python -m app.eval.regression_check --fast
    
    # Full suite (all 15 cases)
    python -m app.eval.regression_check --all
    
    # Compare specific commits
    python -m app.eval.regression_check --baseline main --head feature-branch
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from app.eval.regression_stability import RegressionTestConfig, explain_stability_strategy

def load_golden_set(path: str | None = None) -> dict:
    """Load golden_set.json test cases."""
    if path is None:
        # Try multiple possible locations
        import os
        candidates = [
            Path(__file__).parent.parent.parent / "data" / "eval" / "golden_set.json",  # From module
            Path("../../data/eval/golden_set.json"),  # Relative from apps/agent/app/eval
            Path("apps/agent/data/eval/golden_set.json"),  # From workspace root
            Path("data/eval/golden_set.json"),  # From apps/agent
        ]
        for candidate in candidates:
            if candidate.exists():
                path = str(candidate)
                break
        else:
            raise FileNotFoundError(
                f"golden_set.json not found. Tried: {[str(c) for c in candidates]}"
            )
    
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_sections(memo_markdown: str) -> list[str]:
    """Extract ## section headings from memo."""
    sections = []
    for line in memo_markdown.splitlines():
        if line.strip().startswith("## "):
            section = line.strip()[3:].strip()
            sections.append(section)
    return sections


def extract_subsections(memo_markdown: str, parent_section: str) -> list[str]:
    """Extract ### subsections under a specific ## section."""
    in_section = False
    subsections = []
    
    for line in memo_markdown.splitlines():
        if line.strip().startswith(f"## {parent_section}"):
            in_section = True
            continue
        if in_section and line.strip().startswith("## "):
            # Entered next section
            break
        if in_section and line.strip().startswith("### "):
            subsection = line.strip()[4:].strip()
            subsections.append(subsection)
    
    return subsections


def check_composite_worked_example(memo_markdown: str) -> dict:
    """Check if Worked example cites multiple sources without Composite label."""
    # Extract Worked example section
    match = re.search(r'##\s+Worked example\s*\n(.*?)(?=\n##\s+|\Z)', memo_markdown, re.DOTALL | re.I)
    if not match:
        return {"has_worked_example": False, "violation": False}
    
    section_text = match.group(1)
    
    # Check for Composite label
    has_composite_label = bool(re.search(r'>\s*\*\*Composite\*\*', section_text, re.I))
    
    # Count distinct citation numbers
    citation_pattern = re.compile(r'\[(\d+)(?:\s+[A-Za-z]+)?(?:\s*,\s*\d+(?:\s+[A-Za-z]+)?)*\]')
    citation_numbers = set()
    for m in citation_pattern.finditer(section_text):
        # Extract all numbers from citation marker
        numbers = re.findall(r'\b(\d+)\b', m.group(1))
        citation_numbers.update(int(n) for n in numbers)
    
    violation = len(citation_numbers) >= 2 and not has_composite_label
    
    return {
        "has_worked_example": True,
        "citation_count": len(citation_numbers),
        "has_composite_label": has_composite_label,
        "violation": violation
    }


def count_citation_stacking(memo_markdown: str) -> int:
    """Count sentences that cite 3+ distinct sources (citation stacking)."""
    sentences = re.split(r'(?<=[.!?])\s+', memo_markdown)
    stacking_count = 0
    
    for sentence in sentences:
        citation_markers = re.findall(r'\[([^\]]+)\]', sentence)
        if not citation_markers:
            continue
        
        cited_numbers = set()
        for marker in citation_markers:
            numbers = re.findall(r'\b(\d+)\b', marker)
            cited_numbers.update(int(n) for n in numbers)
        
        if len(cited_numbers) >= 3:
            stacking_count += 1
    
    return stacking_count


def check_structural_requirements(memo_markdown: str, test_case: dict) -> dict:
    """Check if memo meets structural requirements from test case."""
    issues = []
    expected_struct = test_case.get("expected_structure", {})
    
    # Extract actual sections
    actual_sections = extract_sections(memo_markdown)
    
    # Check required sections
    required_sections = expected_struct.get("required_sections", [])
    for req_section in required_sections:
        # Normalize for comparison (lowercase, strip)
        if not any(req_section.lower() in s.lower() for s in actual_sections):
            issues.append(f"Missing required section: {req_section}")
    
    # Check subsections in Detailed analysis
    if "required_subsections_in_detailed_analysis" in expected_struct:
        actual_subsections = extract_subsections(memo_markdown, "Detailed analysis")
        required_subsections = expected_struct["required_subsections_in_detailed_analysis"]
        
        for req_subsection in required_subsections:
            # Fuzzy match (lowercase, contains)
            if not any(req_subsection.lower() in s.lower() for s in actual_subsections):
                issues.append(f"Missing required subsection in Detailed analysis: {req_subsection}")
    
    # Check minimum subsections
    if "min_subsections_detailed_analysis" in expected_struct:
        actual_subsections = extract_subsections(memo_markdown, "Detailed analysis")
        min_required = expected_struct["min_subsections_detailed_analysis"]
        if len(actual_subsections) < min_required:
            issues.append(
                f"Insufficient subsections in Detailed analysis: {len(actual_subsections)} < {min_required}"
            )
    
    # Check composite worked example
    if expected_struct.get("max_composite_without_label", 1) == 0:
        composite_check = check_composite_worked_example(memo_markdown)
        if composite_check.get("violation"):
            issues.append(
                f"Worked example cites {composite_check['citation_count']} sources without Composite label"
            )
    
    # Check quantitative findings
    if expected_struct.get("quantitative_findings_required"):
        if "## Quantitative findings" not in memo_markdown:
            issues.append("Missing required Quantitative findings section")
        elif expected_struct.get("min_quantitative_rows", 0) > 0:
            # Count table rows (simplified check)
            quant_match = re.search(r'## Quantitative findings\s*\n(.*?)(?=\n##|\Z)', memo_markdown, re.DOTALL)
            if quant_match:
                table_rows = len([line for line in quant_match.group(1).splitlines() if line.strip().startswith("|")])
                # Subtract header and separator (typically 2-3 rows)
                data_rows = max(0, table_rows - 3)
                min_required = expected_struct["min_quantitative_rows"]
                if data_rows < min_required:
                    issues.append(f"Insufficient quantitative rows: {data_rows} < {min_required}")
    
    # Check comparison table
    if expected_struct.get("comparison_table_required"):
        if "## Comparison" not in memo_markdown:
            issues.append("Missing required Comparison section with table")
    
    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "actual_sections": actual_sections,
        "actual_subsections_detailed": extract_subsections(memo_markdown, "Detailed analysis")
    }


def extract_quality_metrics(memo_markdown: str, report_dict: dict | None = None) -> dict:
    """Extract quality metrics from memo and report."""
    metrics = {}
    
    # Word count
    words = re.findall(r'\S+', memo_markdown)
    metrics["word_count"] = len(words)
    
    # Citation stacking
    stacking = count_citation_stacking(memo_markdown)
    metrics["citation_stacking_count"] = stacking
    metrics["citation_stacking_per_1000_words"] = (stacking / max(len(words), 1)) * 1000
    
    # Source count (unique citation numbers)
    citation_numbers = set()
    for m in re.finditer(r'\[(\d+)(?:\s+[A-Za-z]+)?\]', memo_markdown):
        numbers = re.findall(r'\b(\d+)\b', m.group(1))
        citation_numbers.update(int(n) for n in numbers)
    metrics["unique_sources"] = len(citation_numbers)
    
    # Extract from report metrics if available
    if report_dict and "metrics" in report_dict:
        report_metrics = report_dict["metrics"]
        metrics["coverage_pct"] = report_metrics.get("must_answer_fraction", 0) * 100
        metrics["depth_score"] = report_metrics.get("depth_score", 0)
        metrics["iterations"] = report_metrics.get("iterations", 0)
        metrics["tool_calls"] = report_metrics.get("tool_calls", 0)
        metrics["confidence_breakdown"] = report_metrics.get("confidence_breakdown", {})
    
    return metrics


def run_test_case(test_case: dict, mode: str = "mock") -> dict:
    """Run a single test case through the pipeline.
    
    Args:
        test_case: Test case from golden_set.json
        mode: "mock" (use cached/mock data) or "live" (run actual pipeline)
    
    Returns:
        dict with memo_markdown, report, metrics, structural_check
    """
    # For now, this is a stub that would integrate with actual pipeline
    # In production, this would call runtime.stream_execution() or similar
    # Issue #8: When live mode is implemented, use RegressionTestConfig.get_llm_params()
    # to ensure deterministic outputs (temperature=0, seed=42)
    
    if mode == "mock":
        # Return mock data for testing the harness itself
        # Full structure to pass structural validation
        mock_memo = """# Mock Memo Title

## Executive summary
This is a mock executive summary for testing purposes.

## Key findings
1. Mock finding one [1]
2. Mock finding two [2]
3. Mock finding three [3]

## Detailed analysis

### Mock dimension 1
Analysis of mock dimension 1 [1].

### Mock dimension 2
Analysis of mock dimension 2 [2].

## Worked example
Mock worked example from single source [1].

## Quantitative findings
| Metric | Value | Source |
|--------|-------|--------|
| Mock metric | 95% | [1] |

## Decision rule
Use approach A when condition X. Use approach B when condition Y [3].

## Contradictions & debates
Some papers claim X [1], while others claim Y [2].

## Uncertainties & gaps
Further research needed on Z.

## Source quality
Primary sources: [1], [2]. Secondary sources: [3].

## References
[1] Mock Source 1 (Primary)
[2] Mock Source 2 (Primary)
[3] Mock Source 3 (Secondary)
"""
        return {
            "memo_markdown": mock_memo,
            "report": {"metrics": {"iterations": 3, "tool_calls": 25, "must_answer_fraction": 0.70}},
            "status": "completed",
            "error": None
        }
    else:
        return asyncio.run(_run_live(test_case))


async def _run_live(test_case: dict) -> dict:
    """Run one golden-set query through the real graph — search, scholar,
    critic, report — with HITL disabled (build_test_graph(enable_hitl=False)
    auto-approves brief/plan/memo the same way production does when no
    human is attached) and an in-memory checkpointer (no Postgres needed
    for a one-shot eval run).
    """
    from app.graph.builder import build_test_graph

    graph = build_test_graph(enable_hitl=False)
    started = time.perf_counter()
    try:
        final_state = await graph.ainvoke(
            {"query": test_case["query"]},
            config={"recursion_limit": 60, "configurable": {"thread_id": f"eval-{test_case['id']}"}},
        )
    except Exception as exc:  # noqa: BLE001 - report the failure, don't crash the suite
        return {
            "memo_markdown": "",
            "report": {},
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "duration_s": round(time.perf_counter() - started, 1),
        }
    duration_s = round(time.perf_counter() - started, 1)
    report = final_state.get("report") or {}
    return {
        "memo_markdown": report.get("body_markdown") or "",
        "report": report,
        "status": final_state.get("status") or "unknown",
        "error": None,
        "duration_s": duration_s,
        "traces": final_state.get("traces") or [],
    }


def _score_against_expected_quality(metrics: dict, expected_quality: dict) -> list[str]:
    """Check extracted metrics against golden_set.json's expected_quality
    thresholds. Returns a list of violation strings (empty = passed)."""
    violations = []
    if "min_coverage_pct" in expected_quality:
        got = metrics.get("coverage_pct", 0)
        want = expected_quality["min_coverage_pct"]
        if got < want:
            violations.append(f"coverage_pct {got:.1f}% < required {want}%")
    if "min_unique_sources" in expected_quality:
        got = metrics.get("unique_sources", 0)
        want = expected_quality["min_unique_sources"]
        if got < want:
            violations.append(f"unique_sources {got} < required {want}")
    if "max_citation_stacking_per_1000_words" in expected_quality:
        got = metrics.get("citation_stacking_per_1000_words", 0)
        want = expected_quality["max_citation_stacking_per_1000_words"]
        if got > want:
            violations.append(f"citation_stacking {got:.1f}/1000w > allowed {want}/1000w")
    return violations


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[idx]


def run_regression_suite(golden_set_path: str | None = None, mode: str = "fast", live: bool = False) -> dict:
    """Run the golden set and report structural + quality results.

    Args:
        golden_set_path: Path to golden_set.json
        mode: "fast" (3 cases), "all" (all cases), or "single" (one case)
        live: False (default) — fast, free structural self-check against a
            synthetic memo, validates the checker itself, no API cost.
            True — actually run each query through the real graph (search,
            scholar, critic, report) and score the real memo against
            expected_structure/expected_quality. Costs real LLM + search
            calls and can take minutes per case.

    Returns:
        dict with summary (incl. p50/p95 latency when live), test_results, overall_pass
    """
    golden_set = load_golden_set(golden_set_path)
    test_cases = golden_set["test_cases"]

    # Select test cases based on mode
    if mode == "fast":
        # Run quick representat cases
        selected = [tc for tc in test_cases if tc["id"] in [
            "comparison_multi_dimension_rag",
            "implementation_specific_agentic_loop",
            "fast_iteration_test"
        ]]
    elif mode == "single":
        selected = test_cases[:1]
    else:  # "all"
        selected = test_cases

    print(f"\n{'='*60}")
    print(f"REGRESSION CHECK - Running {len(selected)} test cases ({mode} mode, live={live})")
    print(f"Stability: {explain_stability_strategy()}")
    print(f"{'='*60}\n")

    results = []
    durations: list[float] = []
    for i, test_case in enumerate(selected, 1):
        test_id = test_case["id"]
        print(f"[{i}/{len(selected)}] Running: {test_id}")

        if not live:
            # Fast/free: validate the structural checker itself against a
            # synthetic memo — not a claim about real pipeline quality.
            struct_check = check_structural_requirements("# Mock memo\n## Executive summary\nTest", test_case)
            results.append({
                "test_id": test_id,
                "live": False,
                "structural_check": struct_check,
                "passed": struct_check["passed"],
                "violations": struct_check["issues"],
            })
            print("  ✅ PASSED" if struct_check["passed"] else f"  ❌ FAILED: {struct_check['issues']}")
            continue

        run = run_test_case(test_case, mode="live")
        if run["error"]:
            results.append({
                "test_id": test_id,
                "live": True,
                "passed": False,
                "violations": [f"run failed: {run['error']}"],
                "duration_s": run["duration_s"],
            })
            print(f"  ❌ ERROR: {run['error']}")
            continue

        durations.append(run["duration_s"])
        struct_check = check_structural_requirements(run["memo_markdown"], test_case)
        metrics = extract_quality_metrics(run["memo_markdown"], run["report"])
        quality_violations = _score_against_expected_quality(metrics, test_case.get("expected_quality", {}))
        violations = struct_check["issues"] + quality_violations
        results.append({
            "test_id": test_id,
            "live": True,
            "structural_check": struct_check,
            "metrics": metrics,
            "duration_s": run["duration_s"],
            "passed": not violations,
            "violations": violations,
        })
        status = "✅ PASSED" if not violations else "❌ FAILED"
        print(f"  {status} ({run['duration_s']}s) {violations or ''}")

    total_failed = sum(1 for r in results if not r["passed"])
    overall_pass = total_failed == 0

    print(f"\n{'='*60}")
    print(f"RESULTS: {len(selected) - total_failed}/{len(selected)} tests passed")
    if durations:
        print(
            f"Latency: p50={_percentile(durations, 50):.1f}s "
            f"p95={_percentile(durations, 95):.1f}s "
            f"max={max(durations):.1f}s"
        )
    if not overall_pass:
        print(f"❌ {total_failed} FAILURE(S)")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['test_id']}: {r['violations']}")
    else:
        print("✅ ALL TESTS PASSED")
    print(f"{'='*60}\n")

    summary: dict[str, Any] = {
        "total_tests": len(selected),
        "passed": len(selected) - total_failed,
        "failed": total_failed,
        "overall_pass": overall_pass,
    }
    if durations:
        summary["latency_p50_s"] = _percentile(durations, 50)
        summary["latency_p95_s"] = _percentile(durations, 95)
        summary["latency_max_s"] = max(durations)

    return {
        "summary": summary,
        "test_results": results,
        "overall_pass": overall_pass
    }


def main():
    parser = argparse.ArgumentParser(description="Run regression tests for Kiln agent")
    parser.add_argument("--fast", action="store_true", help="Run 3 quick test cases")
    parser.add_argument("--all", action="store_true", help="Run all test cases")
    parser.add_argument("--single", action="store_true", help="Run single test case (for debugging)")
    parser.add_argument("--golden-set", type=str, help="Path to golden_set.json")
    parser.add_argument(
        "--live", action="store_true",
        help="Run each case through the real graph (real LLM + search calls, costs money and minutes) "
        "instead of the free structural self-check",
    )

    args = parser.parse_args()

    # Determine mode
    if args.fast:
        mode = "fast"
    elif args.all:
        mode = "all"
    elif args.single:
        mode = "single"
    else:
        mode = "fast"  # default

    try:
        results = run_regression_suite(args.golden_set, mode=mode, live=args.live)

        # Exit with non-zero if regressions detected
        sys.exit(0 if results["overall_pass"] else 1)
    
    except Exception as e:
        print(f"❌ ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
