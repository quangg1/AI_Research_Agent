from app.eval.race_bench import run_race_proxy_bench


def test_race_proxy_bench_runs():
    out = run_race_proxy_bench()
    assert out["cases"] >= 5
    assert "avg_race_proxy" in out
    assert all("race_proxy" in row for row in out["rows"])
