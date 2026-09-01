"""Eval metrics: grounding and must-answer coverage on composed reports."""

from __future__ import annotations

from app.domain.coverage import must_answer_for, score_must_answer
from app.domain.grounding import verify_claims
from app.domain.research_intent import user_goal
from app.report.compose import compose_report


def score_grounding(report, evidence: list[dict]) -> dict:
    claims = report.claims if hasattr(report, "claims") else (report.get("claims") or [])
    claim_dicts = [
        c.model_dump(mode="json") if hasattr(c, "model_dump") else dict(c) for c in claims
    ]
    verified = verify_claims(claim_dicts, evidence)
    grounded = sum(1 for c in verified if c.get("grounded"))
    total = len(verified)
    return {
        "claims": total,
        "grounded": grounded,
        "hallucinated": total - grounded,
        "grounding_rate": round(grounded / max(total, 1), 3),
    }


def score_must_answer_coverage(query: str, evidence: list[dict], slots: list[dict] | None = None) -> dict:
    slots = slots or must_answer_for(query)
    cov = score_must_answer(query, evidence, slots)
    return {
        "ratio": cov.get("ratio"),
        "must_answer_fraction": cov.get("must_answer_fraction"),
        "critical_fraction": cov.get("critical_fraction"),
        "open": cov.get("open"),
        "weak": cov.get("weak"),
        "covered": cov.get("covered"),
        "total": cov.get("total"),
        "slots": cov.get("slots") or [],
    }


def score_compose_pipeline(query: str, evidence: list[dict], *, critic: dict | None = None) -> dict:
    """End-to-end compose metrics without LangGraph (deterministic memo path)."""
    critic = critic or {"status": "sufficient", "coverage": {}}
    report = compose_report(
        query=query,
        evidence=evidence,
        critic=critic,
        llm_mode="heuristic",
        synthesis_status="eval",
    )
    grounding = score_grounding(report, evidence)
    coverage = score_must_answer_coverage(query, evidence)
    body = report.body_markdown or ""
    return {
        "has_memo": bool(body.strip()),
        "has_decision_rule": "## Decision rule" in body,
        "citations": len(report.citations),
        **grounding,
        "must_answer_coverage": coverage.get("must_answer_fraction"),
        "coverage_ratio": coverage.get("ratio"),
    }
