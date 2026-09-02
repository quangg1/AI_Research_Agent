from __future__ import annotations

from app.domain.citations import build_ledger
from app.domain.coverage import claims_from_must_answer, must_answer_for, score_must_answer, tag_evidence_roles
from app.domain.gap_enrich import fetch_gap_evidence, gap_slots, merge_fetched_evidence
from app.graph.serde import dump
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event


def extract_node(state: ResearchState) -> dict:
    query = state.get("query") or ""
    budget = budget_from(state)
    brief = state.get("brief") or {}
    depth = (brief.get("depth") or "standard").lower()
    retrieved = tag_evidence_roles(state.get("retrieved") or state.get("evidence") or [], query)
    on_topic = [e for e in retrieved if not e.get("off_topic")]
    working = on_topic if len(on_topic) >= 3 else retrieved
    
    # EVIDENCE-FIRST DIMENSION REFINEMENT
    # If this is first iteration and we have scholar/high-quality evidence,
    # refine dimensions from paper concepts instead of using generic templates
    should_refine_dimensions = (
        budget.iterations == 1  # First iteration
        and len(working) >= 3  # Have some evidence
        and not brief.get("dimensions_refined")  # Not already refined
        and any(e.get("tier") in {"peer_reviewed", "specialist_research"} for e in working[:10])  # Has papers
    )
    
    if should_refine_dimensions:
        from app.domain.decompose import synthesize_dimensions_from_evidence
        
        # Extract paper-specific dimensions
        paper_dimensions = synthesize_dimensions_from_evidence(query, working, fallback_to_heuristic=True)
        
        # Update brief with refined dimensions
        brief = {
            **brief,
            "must_answer": paper_dimensions,
            "dimensions_refined": True,
        }
        event("extract_dimensions_refined", 
              old_count=len(brief.get("must_answer") or []),
              new_count=len(paper_dimensions),
              paper_specific=sum(1 for d in paper_dimensions if d.get("paper_cite")))
    
    slots = brief.get("must_answer") or must_answer_for(query)
    coverage = score_must_answer(query, working, slots)

    gap_retries = int(state.get("gap_micro_retries") or 0)
    micro_cap = {"quick": 2, "standard": 3, "deep": 4}.get(depth, 2)
    gap_targets = gap_slots(coverage.get("slots") or slots)
    needs_micro = bool(gap_targets) and gap_retries < 1 and budget.remaining_calls >= micro_cap

    micro_fetched = 0
    if needs_micro:
        extra, micro_fetched = fetch_gap_evidence(
            working,
            coverage.get("slots") or slots,
            budget,
            limit=micro_cap,
        )
        if extra:
            working = tag_evidence_roles(merge_fetched_evidence(working, extra), query)
            on_topic = [e for e in working if not e.get("off_topic")]
            working = on_topic if len(on_topic) >= 3 else working
            slots = coverage.get("slots") or slots
            coverage = score_must_answer(query, working, slots)
            gap_retries += 1

    ledger = build_ledger(working, k=20)
    claims = claims_from_must_answer(coverage, working)
    event(
        "extract",
        n=len(ledger),
        coverage=coverage.get("ratio"),
        depth=(coverage.get("depth_score") or {}).get("score"),
        gaps=len(coverage.get("critical_gaps") or []),
        gap_micro=micro_fetched,
    )
    return {
        "retrieved": working,
        "citations": [c.model_dump(mode="json") for c in ledger],
        "claims": claims,
        "brief": {**brief, "must_answer": coverage.get("slots") or slots},
        "budget": dump(budget),
        "gap_micro_retries": gap_retries,
        "status": "extracted",
        "traces": [
            {
                "node": "extract",
                "n": len(ledger),
                "coverage_ratio": coverage.get("ratio"),
                "depth_score": (coverage.get("depth_score") or {}).get("score"),
                "critical_gaps": [g.get("id") for g in (coverage.get("critical_gaps") or [])],
                "gap_micro_fetched": micro_fetched,
                "gap_micro_retries": gap_retries,
            }
        ],
    }
