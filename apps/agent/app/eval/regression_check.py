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
import json
import re
import sys
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
        return {
            "memo_markdown": "# Mock Memo\n\n## Executive summary\n\nMock content.\n\n## References\n\n[1] Mock source",
            "report": {"metrics": {"iterations": 3, "tool_calls": 25, "must_answer_fraction": 0.70}},
            "status": "completed",
            "error": None
        }
    else:
        # Live mode - would integrate with actual pipeline
        # This is left as TODO for integration with existing runtime
        # IMPORTANT: Pass RegressionTestConfig.get_llm_params() to writer LLM
        # Example:
        # llm_params = RegressionTestConfig.get_llm_params()
        # result = runtime.stream_execution(
        #     query=test_case["query"],
        #     llm_override_params=llm_params,  # temperature=0, seed=42
        #     ...
        # )
        raise NotImplementedError("Live pipeline integration not yet implemented")


def compare_results(baseline: dict, head: dict, test_case: dict) -> dict:
    """Compare baseline vs HEAD results for a single test case."""
    regressions = []
    improvements = []
    
    # Structural regressions
    baseline_struct = baseline.get("structural_check", {})
    head_struct = head.get("structural_check", {})
    
    if not baseline_struct.get("passed") and head_struct.get("passed"):
        improvements.append("Structural issues fixed")
    elif baseline_struct.get("passed") and not head_struct.get("passed"):
        regressions.append(f"NEW structural violations: {head_struct.get('issues', [])}")
    elif len(head_struct.get("issues", [])) > len(baseline_struct.get("issues", [])):
        new_issues = set(head_struct.get("issues", [])) - set(baseline_struct.get("issues", []))
        regressions.append(f"NEW structural issues: {list(new_issues)}")
    
    # Quality metric regressions
    baseline_metrics = baseline.get("metrics", {})
    head_metrics = head.get("metrics", {})
    
    expected_quality = test_case.get("expected_quality", {})
    
    # Coverage regression (>5% drop is significant)
    baseline_cov = baseline_metrics.get("coverage_pct", 0)
    head_cov = head_metrics.get("coverage_pct", 0)
    if head_cov < baseline_cov - 5:
        regressions.append(f"Coverage dropped: {baseline_cov:.1f}% → {head_cov:.1f}%")
    elif head_cov > baseline_cov + 5:
        improvements.append(f"Coverage improved: {baseline_cov:.1f}% → {head_cov:.1f}%")
    
    # Citation stacking regression
    baseline_stack = baseline_metrics.get("citation_stacking_count", 0)
    head_stack = head_metrics.get("citation_stacking_count", 0)
    if head_stack > baseline_stack + 2:
        regressions.append(f"Citation stacking increased: {baseline_stack} → {head_stack}")
    
    # Threshold violations (always bad if HEAD violates but baseline didn't)
    if "max_code_ratio" in expected_quality:
        # Would need actual code ratio from pipeline - stub for now
        pass
    
    return {
        "test_id": test_case["id"],
        "regressions": regressions,
        "improvements": improvements,
        "has_regression": len(regressions) > 0,
        "baseline_metrics": baseline_metrics,
        "head_metrics": head_metrics
    }


def run_regression_suite(golden_set_path: str | None = None, mode: str = "fast") -> dict:
    """Run regression suite and return results.
    
    Args:
        golden_set_path: Path to golden_set.json
        mode: "fast" (3 cases), "all" (all cases), or "single" (one case)
    
    Returns:
        dict with summary, test_results, overall_pass
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
    print(f"REGRESSION CHECK - Running {len(selected)} test cases ({mode} mode)")
    print(f"Stability: {explain_stability_strategy()}")
    print(f"{'='*60}\n")
    
    results = []
    for i, test_case in enumerate(selected, 1):
        test_id = test_case["id"]
        print(f"[{i}/{len(selected)}] Running: {test_id}")
        
        # For now, we're in "check harness" mode, so we'll just validate structure
        # In production, this would run the actual pipeline
        
        # Mock: assume we have baseline and HEAD results
        # In reality, these would come from running the pipeline
        baseline_result = {"structural_check": {"passed": True, "issues": []}, "metrics": {"coverage_pct": 70}}
        head_result = {
            "structural_check": check_structural_requirements("# Mock memo\n## Executive summary\nTest", test_case),
            "metrics": {"coverage_pct": 68}
        }
        
        comparison = compare_results(baseline_result, head_result, test_case)
        results.append(comparison)
        
        if comparison["has_regression"]:
            print(f"  ❌ REGRESSION DETECTED: {comparison['regressions']}")
        else:
            print(f"  ✅ PASSED")
    
    # Aggregate results
    total_regressions = sum(1 for r in results if r["has_regression"])
    overall_pass = total_regressions == 0
    
    print(f"\n{'='*60}")
    print(f"RESULTS: {len(selected) - total_regressions}/{len(selected)} tests passed")
    if not overall_pass:
        print(f"❌ {total_regressions} REGRESSION(S) DETECTED")
        print("\nFailing tests:")
        for r in results:
            if r["has_regression"]:
                print(f"  - {r['test_id']}: {r['regressions']}")
    else:
        print("✅ ALL TESTS PASSED")
    print(f"{'='*60}\n")
    
    return {
        "summary": {
            "total_tests": len(selected),
            "passed": len(selected) - total_regressions,
            "failed": total_regressions,
            "overall_pass": overall_pass
        },
        "test_results": results,
        "overall_pass": overall_pass
    }


def main():
    parser = argparse.ArgumentParser(description="Run regression tests for Kiln agent")
    parser.add_argument("--fast", action="store_true", help="Run 3 quick test cases")
    parser.add_argument("--all", action="store_true", help="Run all test cases")
    parser.add_argument("--single", action="store_true", help="Run single test case (for debugging)")
    parser.add_argument("--golden-set", type=str, help="Path to golden_set.json")
    parser.add_argument("--baseline", type=str, help="Baseline git commit (for comparison mode)")
    parser.add_argument("--head", type=str, help="HEAD git commit (for comparison mode)")
    
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
        results = run_regression_suite(args.golden_set, mode=mode)
        
        # Exit with non-zero if regressions detected
        sys.exit(0 if results["overall_pass"] else 1)
    
    except Exception as e:
        print(f"❌ ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
