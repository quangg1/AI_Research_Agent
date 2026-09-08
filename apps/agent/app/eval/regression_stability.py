"""Regression test stability configuration for stochastic LLM outputs.

Addresses Issue #8: Baseline-vs-HEAD comparison with temperature > 0 can cause
flaky CI due to natural output variance.

Strategies:
1. Deterministic mode: temperature=0 for regression tests
2. N-run consensus: Run each test N times, use majority/aggregate
3. Statistical validation: Compare distributions, not single samples

Default: Deterministic mode (temperature=0) for speed + reliability
Fallback: N-run consensus if deterministic mode unavailable
"""

from __future__ import annotations

from typing import Any


class RegressionTestConfig:
    """Configuration for stable regression testing."""
    
    # Test execution strategy
    STRATEGY = "deterministic"  # "deterministic" | "n_run_consensus" | "statistical"
    
    # Deterministic mode settings
    DETERMINISTIC_TEMPERATURE = 0.0
    DETERMINISTIC_SEED = 42
    
    # N-run consensus settings
    N_RUNS = 3  # Number of runs per test case
    CONSENSUS_THRESHOLD = 0.67  # 2/3 agreement required
    
    # Statistical comparison settings
    STATISTICAL_RUNS = 5
    VARIANCE_THRESHOLD = 0.15  # Max acceptable variance between baseline/HEAD
    
    # Flake detection
    MAX_RETRIES = 2  # Retry flaky tests
    FLAKE_THRESHOLD = 0.30  # If >30% tests flaky, flag CI config issue
    
    @classmethod
    def get_llm_params(cls) -> dict[str, Any]:
        """Get LLM parameters for deterministic execution.
        
        Returns:
            dict with temperature, seed, etc. for stable outputs
        """
        if cls.STRATEGY == "deterministic":
            return {
                "temperature": cls.DETERMINISTIC_TEMPERATURE,
                "seed": cls.DETERMINISTIC_SEED,
                "top_p": 1.0,  # Disable nucleus sampling
                "top_k": None,  # Disable top-k sampling
            }
        else:
            # For n-run or statistical, use default creative params
            return {
                "temperature": 0.7,
                "seed": None,
            }
    
    @classmethod
    def should_use_n_runs(cls) -> bool:
        """Check if N-run consensus is enabled."""
        return cls.STRATEGY in ["n_run_consensus", "statistical"]
    
    @classmethod
    def get_n_runs(cls) -> int:
        """Get number of runs per test."""
        if cls.STRATEGY == "deterministic":
            return 1
        elif cls.STRATEGY == "n_run_consensus":
            return cls.N_RUNS
        else:  # statistical
            return cls.STATISTICAL_RUNS


def aggregate_n_run_results(results: list[dict]) -> dict:
    """Aggregate N runs of same test into consensus result.
    
    Args:
        results: List of N result dicts from same test case
    
    Returns:
        Aggregated result with consensus metrics
    """
    if len(results) == 1:
        return results[0]
    
    # Aggregate structural results (majority vote)
    all_structural = [r.get("structural_check", {}) for r in results]
    
    # Count how many runs passed structural checks
    passed_count = sum(1 for s in all_structural if s.get("passed", False))
    consensus_passed = passed_count >= len(results) * RegressionTestConfig.CONSENSUS_THRESHOLD
    
    # Aggregate issues (union of all issues seen)
    all_issues = []
    for s in all_structural:
        all_issues.extend(s.get("issues", []))
    unique_issues = list(set(all_issues))
    
    # Aggregate metrics (average)
    all_metrics = [r.get("metrics", {}) for r in results]
    avg_metrics = {}
    if all_metrics:
        metric_keys = set()
        for m in all_metrics:
            metric_keys.update(m.keys())
        
        for key in metric_keys:
            values = [m.get(key, 0) for m in all_metrics if key in m]
            if values:
                avg_metrics[key] = sum(values) / len(values)
    
    return {
        "structural_check": {
            "passed": consensus_passed,
            "issues": unique_issues,
            "consensus_confidence": passed_count / len(results),
            "n_runs": len(results),
        },
        "metrics": avg_metrics,
        "variance_detected": len(unique_issues) > 0 and not consensus_passed,
    }


def detect_flaky_test(results: list[dict]) -> bool:
    """Detect if a test is flaky based on N-run variance.
    
    Args:
        results: List of N result dicts from same test case
    
    Returns:
        True if test shows high variance (likely flaky)
    """
    if len(results) <= 1:
        return False
    
    # Check structural variance
    all_passed = [r.get("structural_check", {}).get("passed", False) for r in results]
    
    # Flaky if results are inconsistent
    if 0 < sum(all_passed) < len(all_passed):
        return True
    
    # Check metric variance
    all_metrics = [r.get("metrics", {}) for r in results]
    for key in ["coverage_pct", "word_count"]:
        values = [m.get(key, 0) for m in all_metrics if key in m]
        if len(values) > 1:
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std_dev = variance ** 0.5
            coefficient_of_variation = (std_dev / mean) if mean > 0 else 0
            
            # High variance = flaky
            if coefficient_of_variation > RegressionTestConfig.VARIANCE_THRESHOLD:
                return True
    
    return False


def explain_stability_strategy() -> str:
    """Return human-readable explanation of current stability strategy."""
    strategy = RegressionTestConfig.STRATEGY
    
    if strategy == "deterministic":
        return (
            f"Deterministic mode: temperature={RegressionTestConfig.DETERMINISTIC_TEMPERATURE}, "
            f"seed={RegressionTestConfig.DETERMINISTIC_SEED}. "
            "Single run per test, stable outputs."
        )
    elif strategy == "n_run_consensus":
        return (
            f"N-run consensus: {RegressionTestConfig.N_RUNS} runs per test, "
            f"{RegressionTestConfig.CONSENSUS_THRESHOLD:.0%} agreement required. "
            "Averages metrics, majority vote on pass/fail."
        )
    else:  # statistical
        return (
            f"Statistical mode: {RegressionTestConfig.STATISTICAL_RUNS} runs per test, "
            f"variance threshold={RegressionTestConfig.VARIANCE_THRESHOLD:.0%}. "
            "Compares distributions, detects significant changes."
        )
