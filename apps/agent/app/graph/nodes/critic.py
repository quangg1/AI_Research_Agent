from __future__ import annotations

from app.domain.coverage import (
    claims_from_must_answer,
    critic_should_pass,
    followups_for_gaps,
    must_answer_for,
    score_must_answer,
    tag_evidence_roles,
)
from app.domain.research_intent import topic_leakage_reasons, user_goal
from app.domain.schema import CriticVerdict
from app.graph.serde import dump
from app.graph.state import ResearchState, budget_from
from app.llm.client import llm
from app.observability.logging import event


def critic_node(state: ResearchState) -> dict:
    query = state.get("query") or ""
    retrieved = tag_evidence_roles(state.get("retrieved") or state.get("evidence") or [], query)
    budget = budget_from(state)
    brief = state.get("brief") or {}
    slots = brief.get("must_answer") or must_answer_for(query)
    coverage = score_must_answer(query, retrieved, slots)

    verdict = _llm_critic(state, retrieved, coverage) or _heuristic_critic(state, retrieved, coverage, budget)
    if verdict.status in {"ok", "pass", "good", "pass_with_notes"}:
        verdict.status = "sufficient"

    # Hard gate: coverage critic overrides LLM "sufficient"
    ok, gap_reasons = critic_should_pass(query, coverage, retrieved)
    leaks = topic_leakage_reasons(query, retrieved)
    reasons = list(dict.fromkeys([*(verdict.reasons or []), *gap_reasons, *leaks]))
    verdict.reasons = reasons
    verdict.coverage = {
        "ratio": coverage.get("ratio"),
        "covered": coverage.get("covered"),
        "weak": coverage.get("weak"),
        "open": coverage.get("open"),
        "total": coverage.get("total"),
        "must_answer_fraction": coverage.get("must_answer_fraction"),
        "critical_fraction": coverage.get("critical_fraction"),
        "critical_ratio": coverage.get("critical_ratio"),
        "slots": [
            {
                "id": s["id"],
                "label": s["label"],
                "status": s["status"],
                "critical": s.get("critical"),
                "priority": s.get("priority"),
                "evidence_ids": s.get("evidence_ids") or [],
                "evidence_type": s.get("evidence_type"),
            }
            for s in (coverage.get("slots") or [])
        ],
        "critical_gaps": coverage.get("critical_gaps") or [],
        "weak_slots": coverage.get("weak_slots") or [],
        "strong_slots": coverage.get("strong_slots") or [],
        "has_implementation": coverage.get("has_implementation"),
        "official_impl_count": coverage.get("official_impl_count"),
        "unique_sources": coverage.get("unique_sources"),
        "primary_sources": coverage.get("primary_sources"),
        "roles_present": coverage.get("roles_present") or [],
        "contradictions": coverage.get("contradictions") or [],
    }
    verdict.depth_score = coverage.get("depth_score") or {}

    can_loop = budget.remaining_iterations > 0 and budget.remaining_calls > 0
    if not ok:
        verdict.status = "insufficient" if verdict.status != "contradicted" else verdict.status
        if can_loop:
            verdict.followup_queries = followups_for_gaps(query, coverage, limit=2)
    elif verdict.status == "sufficient" and not can_loop:
        pass
    elif ok and verdict.status != "contradicted":
        verdict.status = "sufficient"
        verdict.followup_queries = []

    if not can_loop:
        # Budget exhausted: preserve follow-up intent for terminal synthesis
        pending = verdict.followup_queries or (followups_for_gaps(query, coverage, limit=2) if not ok else [])
        stored_followups = [dump(q) for q in pending]
        verdict.followup_queries = []
        if not ok and verdict.status == "sufficient":
            verdict.status = "insufficient"
    else:
        stored_followups = []

    claims = state.get("claims") or claims_from_must_answer(coverage, retrieved)

    event(
        "critic",
        status=verdict.status,
        followups=len(verdict.followup_queries),
        iteration=budget.iterations,
        coverage=coverage.get("ratio"),
        depth=(coverage.get("depth_score") or {}).get("score"),
    )
    return {
        "critic": dump(verdict),
        "followups": [dump(q) for q in verdict.followup_queries],
        "terminal_followups": _merge_terminal_followups(state.get("terminal_followups") or [], stored_followups),
        "claims": claims,
        "brief": {**brief, "must_answer": coverage.get("slots") or slots},
        "budget": dump(budget),
        "llm_mode": llm.mode,
        "traces": [
            {
                "node": "critic",
                "status": verdict.status,
                "reasons": verdict.reasons[:6],
                "followups": len(verdict.followup_queries),
                "coverage_ratio": coverage.get("ratio"),
                "depth_score": (coverage.get("depth_score") or {}).get("score"),
                "depth_label": (coverage.get("depth_score") or {}).get("label"),
                "gaps": [g.get("id") for g in (coverage.get("critical_gaps") or [])],
            }
        ],
    }


def _merge_terminal_followups(existing: list, new: list) -> list:
    out = list(existing)
    seen = {(f.get("question") if isinstance(f, dict) else str(f)) for f in existing}
    for f in new:
        key = f.get("question") if isinstance(f, dict) else str(f)
        if key and key not in seen:
            out.append(f)
            seen.add(key)
    return out


def _heuristic_critic(state: ResearchState, evidence: list[dict], coverage: dict, budget) -> CriticVerdict:
    query = state.get("query") or ""
    if not evidence:
        return CriticVerdict(
            status="insufficient",
            reasons=["No evidence collected."],
            followup_queries=followups_for_gaps(query, coverage),
            confidence_floor=0.2,
            coverage={},
            depth_score=coverage.get("depth_score") or {},
        )
    reasons: list[str] = []
    disagreements = coverage.get("contradictions") or []
    contradicted = len(disagreements) >= 2
    reasons.extend(disagreements[:3])
    ok, gap_reasons = critic_should_pass(query, coverage, evidence)
    reasons.extend(gap_reasons)
    can_loop = budget.remaining_iterations > 0 and budget.remaining_calls > 0
    if contradicted and ok:
        # Contradiction with good coverage → contradicted but may still proceed after noting
        return CriticVerdict(
            status="contradicted",
            reasons=reasons,
            followup_queries=followups_for_gaps(query, coverage) if can_loop else [],
            confidence_floor=0.62,
        )
    if ok:
        return CriticVerdict(
            status="sufficient",
            reasons=reasons or ["Must-answer coverage met with primary-source evidence."],
            confidence_floor=0.8,
        )
    return CriticVerdict(
        status="insufficient",
        reasons=reasons or ["Must-answer coverage incomplete."],
        followup_queries=followups_for_gaps(query, coverage) if can_loop else [],
        confidence_floor=0.45,
    )


def _llm_critic(state: ResearchState, evidence: list[dict], coverage: dict) -> CriticVerdict | None:
    if not llm.available:
        return None
    payload = llm.generate_json(
        prompt=(
            f"Question: {user_goal(state['query'])}\n"
            f"Must-answer coverage:\n{coverage}\n"
            f"Evidence (roles tagged): {[{'title': e.get('title'), 'url': e.get('url'), 'role': e.get('source_role'), 'tier': e.get('tier')} for e in evidence[:12]]}\n"
            "You are a coverage critic, not a prose critic. Judge whether the evidence can support an "
            "answer to THIS question, whatever its subject.\n"
            "status=sufficient only if every critical dimension is covered by a source that speaks to it "
            "directly. Background material that merely shares vocabulary with the question does not count.\n"
            "If the question asks how something works or is built, generic overviews are not sufficient; "
            "a primary or source-level document is required.\n"
            "If gaps remain, status=insufficient and propose followup_queries targeting those gaps.\n"
            "JSON: status, reasons, followup_queries, claimed_unsupported, confidence_floor."
        ),
        system="Kiln coverage critic. Refuse 'sufficient' when a critical dimension lacks direct evidence.",
    )
    if not isinstance(payload, dict):
        return None
    try:
        return CriticVerdict.model_validate(payload)
    except Exception:
        return None
