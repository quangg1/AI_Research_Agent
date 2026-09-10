from app.eval.regression_check import (
    _percentile,
    _score_against_expected_quality,
    run_regression_suite,
)


def test_score_against_expected_quality_flags_shortfall():
    metrics = {"coverage_pct": 40, "unique_sources": 2, "citation_stacking_per_1000_words": 5}
    expected = {
        "min_coverage_pct": 65,
        "min_unique_sources": 6,
        "max_citation_stacking_per_1000_words": 3,
    }
    violations = _score_against_expected_quality(metrics, expected)
    assert len(violations) == 3


def test_score_against_expected_quality_passes_when_met():
    metrics = {"coverage_pct": 80, "unique_sources": 8, "citation_stacking_per_1000_words": 1}
    expected = {
        "min_coverage_pct": 65,
        "min_unique_sources": 6,
        "max_citation_stacking_per_1000_words": 3,
    }
    assert _score_against_expected_quality(metrics, expected) == []


def test_percentile_basic():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(values, 50) == 30.0
    assert _percentile(values, 0) == 10.0
    assert _percentile(values, 100) == 50.0


def test_run_regression_suite_mock_mode_does_not_run_live_pipeline():
    """live=False must stay free/fast — no graph invocation, no API calls."""
    result = run_regression_suite(mode="fast", live=False)
    assert result["summary"]["total_tests"] == 3
    assert "latency_p50_s" not in result["summary"]
    for r in result["test_results"]:
        assert r["live"] is False
