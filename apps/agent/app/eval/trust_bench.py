"""Tier-A trustworthiness benchmark.

Measures precision/recall of Kiln's own grounding gates (report_integrity,
quantitative_verify, citations, memo_quality) against a labeled set of real
fabrication/misattribution cases pulled from production runs, plus matched
negative controls so a checker that just flags everything can't score well.

Pure code, no LLM calls — cheap enough to run on every push. See
`run_trust_bench()` for the aggregate report and `data/eval/trust_bench_cases.json`
for the case data (each entry documents which real run/bug it came from).
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _eval_path(name: str) -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "eval" / name
        if candidate.exists():
            return candidate
    return Path(f"../../data/eval/{name}")


def _check_semantic_number_grounded(case: dict) -> tuple[bool, str]:
    from app.domain.quantitative_verify import semantic_number_grounded

    ok, note = semantic_number_grounded(case["input"]["row"], case["input"]["source"])
    return (not ok, note)


def _check_verify_quantitative_row(case: dict) -> tuple[bool, str]:
    from app.domain.quantitative_verify import verify_quantitative_row

    inp = case["input"]
    result = verify_quantitative_row(inp["row"], citations=inp["citations"], evidence=inp["evidence"])
    return (result["status"] != "verified", result["status"])


def _check_decision_rule_number_stripped(case: dict) -> tuple[bool, str]:
    from app.domain.report_integrity import enforce_report_integrity

    inp = case["input"]
    out = enforce_report_integrity(
        body_markdown=inp.get("body_markdown", "## Quantitative findings\n\nMeasurements absent in source texts.\n\n"),
        executive_summary=inp.get("executive_summary", "Analytical summary."),
        decision_rule=inp["decision_rule"],
        at_a_glance="",
        citations=inp["citations"],
        critic={"depth_score": {"score": 68, "label": "standard"}},
        limitations=[],
        evidence=inp.get("evidence"),
        query=inp.get("query", ""),
    )
    flagged = inp["marker"] not in out["decision_rule"]
    return (flagged, out["decision_rule"][:200])


def _check_entity_citation_stripped(case: dict) -> tuple[bool, str]:
    from app.domain.report_integrity import enforce_report_integrity

    inp = case["input"]
    out = enforce_report_integrity(
        body_markdown=inp["body_markdown"],
        executive_summary=inp.get("executive_summary", "Comparison of agent orchestration frameworks."),
        decision_rule=inp.get("decision_rule", ""),
        at_a_glance="",
        citations=inp["citations"],
        critic={"depth_score": {"score": 68, "label": "standard"}},
        limitations=[],
        evidence=inp["evidence"],
        query=inp["query"],
    )
    lines = out["body_markdown"].splitlines()
    line = next((ln for ln in lines if inp["line_marker"] in ln), "")
    flagged = inp["citation_marker"] not in line
    return (flagged, line)


def _check_reference_dropped_if_uncited(case: dict) -> tuple[bool, str]:
    from app.domain.citations import bind_markdown_to_ledger

    inp = case["input"]
    out = bind_markdown_to_ledger(inp["markdown"], inp["citations"])
    flagged = inp["marker"] not in out
    return (flagged, out[-300:])


def _check_placeholder_not_flagged(case: dict) -> tuple[bool, str]:
    from app.domain.memo_quality import check_memo_quality

    quality = check_memo_quality(case["input"]["memo"])
    flagged = quality["template_placeholder_count"] > 0
    return (flagged, str(quality["template_placeholder_count"]))


CHECKERS: dict[str, Callable[[dict], tuple[bool, str]]] = {
    "semantic_number_grounded": _check_semantic_number_grounded,
    "verify_quantitative_row": _check_verify_quantitative_row,
    "decision_rule_number_stripped": _check_decision_rule_number_stripped,
    "entity_citation_stripped": _check_entity_citation_stripped,
    "reference_dropped_if_uncited": _check_reference_dropped_if_uncited,
    "template_placeholder_not_flagged": _check_placeholder_not_flagged,
}


def load_cases() -> list[dict]:
    return json.loads(_eval_path("trust_bench_cases.json").read_text(encoding="utf-8"))


def run_case(case: dict) -> dict:
    flagged, detail = CHECKERS[case["checker"]](case)
    expected = case["expected_flagged"]
    if expected and flagged:
        outcome = "true_positive"
    elif expected and not flagged:
        outcome = "false_negative"
    elif not expected and not flagged:
        outcome = "true_negative"
    else:
        outcome = "false_positive"
    return {**case, "flagged": flagged, "detail": detail, "outcome": outcome, "correct": outcome in ("true_positive", "true_negative")}


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def run_trust_bench(save_history: bool = True) -> dict:
    results = [run_case(c) for c in load_cases()]
    tp = sum(r["outcome"] == "true_positive" for r in results)
    fp = sum(r["outcome"] == "false_positive" for r in results)
    fn = sum(r["outcome"] == "false_negative" for r in results)
    tn = sum(r["outcome"] == "true_negative" for r in results)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    by_category: dict[str, dict] = {}
    for r in results:
        cat = by_category.setdefault(r["category"], {"total": 0, "correct": 0})
        cat["total"] += 1
        cat["correct"] += int(r["correct"])

    summary = {
        "n_cases": len(results),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "by_category": by_category,
        "failures": [
            {"id": r["id"], "category": r["category"], "outcome": r["outcome"], "detail": r["detail"][:200]}
            for r in results
            if not r["correct"]
        ],
    }

    if save_history:
        history_path = _eval_path("trust_bench_history.jsonl")
        if not history_path.exists():
            here = Path(__file__).resolve()
            for parent in here.parents:
                if (parent / "data" / "eval").is_dir():
                    history_path = parent / "data" / "eval" / "trust_bench_history.jsonl"
                    break
        row = {
            "date": datetime.now(timezone.utc).isoformat(),
            "git_sha": _git_sha(),
            "n_cases": summary["n_cases"],
            "precision": summary["precision"],
            "recall": summary["recall"],
            "f1": summary["f1"],
        }
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    return summary


def main() -> None:
    summary = run_trust_bench()
    print(
        f"Trust bench: {summary['n_cases']} cases | "
        f"precision={summary['precision']} recall={summary['recall']} f1={summary['f1']}"
    )
    print(f"  TP={summary['tp']} FP={summary['fp']} FN={summary['fn']} TN={summary['tn']}")
    for cat, stats in summary["by_category"].items():
        print(f"  {cat}: {stats['correct']}/{stats['total']}")
    if summary["failures"]:
        print("Failures:")
        for f in summary["failures"]:
            print(f"  [{f['outcome']}] {f['id']} ({f['category']}): {f['detail']}")


if __name__ == "__main__":
    main()
