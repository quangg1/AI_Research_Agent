from __future__ import annotations

from app.domain.citations import build_ledger
from app.domain.coverage import claims_from_must_answer, must_answer_for, score_must_answer, tag_evidence_roles
from app.graph.state import ResearchState
from app.observability.logging import event


def extract_node(state: ResearchState) -> dict:
    query = state.get("query") or ""
    retrieved = tag_evidence_roles(state.get("retrieved") or state.get("evidence") or [], query)
    # Drop hard off-topic rows from the working set used for claims/ledger when better sources exist
    on_topic = [e for e in retrieved if not e.get("off_topic")]
    working = on_topic if len(on_topic) >= 3 else retrieved
    brief = state.get("brief") or {}
    slots = brief.get("must_answer") or must_answer_for(query)
    coverage = score_must_answer(query, working, slots)
    ledger = build_ledger(working, k=14)
    claims = claims_from_must_answer(coverage, working)
    event(
        "extract",
        n=len(ledger),
        coverage=coverage.get("ratio"),
        depth=(coverage.get("depth_score") or {}).get("score"),
        gaps=len(coverage.get("critical_gaps") or []),
    )
    return {
        "retrieved": working,
        "citations": [c.model_dump(mode="json") for c in ledger],
        "claims": claims,
        "brief": {**brief, "must_answer": coverage.get("slots") or slots},
        "status": "extracted",
        "traces": [
            {
                "node": "extract",
                "n": len(ledger),
                "coverage_ratio": coverage.get("ratio"),
                "depth_score": (coverage.get("depth_score") or {}).get("score"),
                "critical_gaps": [g.get("id") for g in (coverage.get("critical_gaps") or [])],
            }
        ],
        # Stash coverage for critic via critic merge — also put on a dedicated key through traces;
        # critic recomputes from evidence for safety.
    }
