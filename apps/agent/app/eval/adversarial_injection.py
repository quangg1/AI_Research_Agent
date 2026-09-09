"""Adversarial injection eval fixtures (Tier 0)."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.untrusted_content import (
    contains_injection_markers,
    injection_resisted_in_output,
    sanitize_evidence_row,
)
from app.tools.fetch import sanitize_fetched_content


def _fixture_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "eval" / "adversarial_injection.json"
        if candidate.exists():
            return candidate
    return Path("data/eval/adversarial_injection.json")


def load_cases() -> list[dict]:
    return json.loads(_fixture_path().read_text(encoding="utf-8"))["cases"]


def score_case(case: dict) -> dict:
    snippet = case["snippet"]
    cleaned = sanitize_fetched_content(snippet)
    row = sanitize_evidence_row({"snippet": snippet, "quote": snippet, "url": "https://evil.example/x"})
    sanitized = row.get("snippet") or ""
    markers_gone = not contains_injection_markers(sanitized)
    memo_safe = injection_resisted_in_output(cleaned, case.get("forbidden_phrases") or [])
    return {
        "id": case["id"],
        "pass": markers_gone and memo_safe,
        "markers_gone": markers_gone,
        "memo_safe": memo_safe,
    }


def run_eval() -> dict:
    cases = load_cases()
    scored = [score_case(c) for c in cases]
    passed = sum(1 for s in scored if s["pass"])
    return {"total": len(scored), "passed": passed, "cases": scored}
