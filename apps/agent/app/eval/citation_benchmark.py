"""Citation recall benchmark against gold fixtures (Tier 1)."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.citations import build_ledger


def _gold_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "eval" / "citation_gold.json"
        if candidate.exists():
            return candidate
    return Path("data/eval/citation_gold.json")


def load_cases() -> list[dict]:
    return json.loads(_gold_path().read_text(encoding="utf-8"))["cases"]


def score_case(case: dict) -> dict:
    evidence = case.get("evidence") or []
    ledger = build_ledger(evidence)
    urls = {str(c.url or "").rstrip("/").lower() for c in ledger}
    required = [str(u).rstrip("/").lower() for u in (case.get("required_urls") or [])]
    hits = [u for u in required if u in urls]
    min_citations = int(case.get("min_citations") or 1)
    recall = (len(hits) / len(required)) if required else 1.0
    count_ok = len(ledger) >= min_citations
    return {
        "id": case["id"],
        "pass": recall >= 1.0 and count_ok,
        "recall": recall,
        "citation_count": len(ledger),
        "required_hits": hits,
    }


def run_benchmark() -> dict:
    cases = load_cases()
    scored = [score_case(c) for c in cases]
    passed = sum(1 for s in scored if s["pass"])
    return {"total": len(scored), "passed": passed, "cases": scored}
