from app.eval.citation_benchmark import run_benchmark


def test_citation_benchmark_passes_gold_set():
    result = run_benchmark()
    assert result["total"] >= 1
    assert result["passed"] == result["total"]
