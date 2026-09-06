from app.eval.trust_bench import load_cases, run_case, run_trust_bench


def test_trust_bench_f1_at_or_above_floor():
    """CI regression gate: the aggregate grounding-fidelity score must not
    drop below the level already achieved. Raise this floor deliberately
    when a fix genuinely improves it — never lower it to make a change pass."""
    summary = run_trust_bench(save_history=False)
    assert summary["precision"] >= 0.9, summary["failures"]
    assert summary["recall"] >= 0.9, summary["failures"]
    assert summary["f1"] >= 0.9, summary["failures"]


def test_trust_bench_every_case_passes():
    """Same data as the aggregate check, but reported per-case so a single
    regression names the exact case and real-bug source instead of only
    moving the aggregate score."""
    failures = [r for r in (run_case(c) for c in load_cases()) if not r["correct"]]
    assert not failures, [
        f"{r['id']} ({r['category']}): expected_flagged={r['expected_flagged']} got flagged={r['flagged']} — {r['source_bug']}"
        for r in failures
    ]
