"""Structured decision payload alongside memo markdown (Tier 3)."""

from __future__ import annotations

from typing import Any


def build_decision_payload(
    *,
    query: str,
    title: str,
    executive_summary: str,
    decision_rule: str,
    at_a_glance: str,
    claims: list[dict],
    citations: list[dict],
    open_questions: list[str],
    limitations: list[str],
    critic: dict | None = None,
    coverage: dict | None = None,
    metrics: dict | None = None,
) -> dict[str, Any]:
    """Machine-readable decision object for API consumers."""
    critic = critic or {}
    coverage = coverage or {}
    metrics = metrics or {}
    depth = critic.get("depth_score") if isinstance(critic.get("depth_score"), dict) else {}
    return {
        "query": query,
        "title": title,
        "executive_summary": executive_summary,
        "at_a_glance": at_a_glance,
        "decision_rule": decision_rule,
        "claims": [
            {
                "id": c.get("id"),
                "text": c.get("text"),
                "confidence": c.get("confidence"),
                "support_ids": c.get("support_ids") or [],
                "kind": c.get("kind"),
            }
            for c in (claims or [])
            if isinstance(c, dict)
        ],
        "citations": [
            {
                "n": c.get("n"),
                "title": c.get("title"),
                "url": c.get("url"),
                "year": c.get("year"),
                "tier": c.get("tier"),
            }
            for c in (citations or [])
            if isinstance(c, dict)
        ],
        "open_questions": [str(q) for q in (open_questions or []) if str(q).strip()],
        "limitations": [str(x) for x in (limitations or []) if str(x).strip()],
        "coverage": {
            "must_answer": coverage.get("must_answer") or coverage.get("slots"),
            "gaps": coverage.get("gaps") or coverage.get("open_gaps"),
        },
        "confidence": {
            "depth_score": depth.get("score"),
            "critic_floor": critic.get("confidence_floor"),
            "synthesis_status": metrics.get("synthesis_status"),
        },
    }
