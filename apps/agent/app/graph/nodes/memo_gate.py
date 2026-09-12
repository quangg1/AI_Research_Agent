from __future__ import annotations

from langgraph.types import interrupt

from app.conf.thresholds import QualityThresholds
from app.domain.knowledge import depth_of, mark_reused, save_answer
from app.domain.structure_validation import validate_memo_structure
from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState
from app.observability.logging import event
# TEMPORARILY DISABLED: from app.maintenance.hitl_timeout import add_interrupt_metadata



def _mandatory_hard_fail_patch(state: ResearchState, report: dict) -> dict | None:
    """If contract mandatory primaries are missing, block soft-publish.

    Prefer one retrieval pass toward the missing arXiv ids (status=revising → critic).
    After that retry is exhausted, keep Uncertainties but refuse green auto-publish.
    """
    metrics = (report or {}).get("metrics") or {}
    audit = metrics.get("constraint_audit") or {}
    missing = list(audit.get("missing_mandatory") or [])
    hard = bool(audit.get("hard_fail") or audit.get("should_block_publish"))
    if not hard and not missing:
        flags = audit.get("flags") or []
        hard = any(
            str(f).startswith("mandatory_missing_")
            or f in {"hard_fail_mandatory_missing", "should_block_publish"}
            for f in flags
        )
    if not hard and not missing:
        return None

    retries = int(state.get("mandatory_retrieval_retries") or 0)
    if retries < 1 and missing:
        from app.domain.schema import AgentName, SubQuery

        followups = []
        for src in missing[:4]:
            aid = (src.get("arxiv_id") or "").strip()
            label = (src.get("label") or aid or "mandatory primary").strip()
            q = f"arxiv.org/abs/{aid} {label}" if aid else label
            followups.append(
                dump(
                    SubQuery(
                        agent=AgentName.SCHOLAR,
                        question=q[:200],
                        rationale=f"Retrieve mandatory primary: {label}",
                    )
                )
            )
        if followups:
            event(
                "memo_gate_mandatory_retrieval",
                missing=[s.get("arxiv_id") for s in missing],
                attempt=retries + 1,
            )
            return {
                "status": "revising",
                "memo_confirmed": False,
                "followups": followups,
                "mandatory_retrieval_retries": retries + 1,
                "traces": [
                    {
                        "node": "memo_gate",
                        "action": "mandatory_retrieval",
                        "missing": [s.get("arxiv_id") for s in missing],
                        "attempt": retries + 1,
                    }
                ],
            }

    return {
        "_mandatory_block": True,
        "missing_mandatory": missing,
        "hard_fail": True,
    }



def memo_gate_node(state: ResearchState) -> dict:
    """Review the written memo draft before publishing to the user."""
    from app.domain.memo_quality import check_memo_quality, declutter_citations

    report = state.get("report") or {}
    has_draft = bool((report.get("body_markdown") or "").strip())
    status = str(state.get("status") or "")
    
    # Configuration: Enable semantic validation (high compute cost, OFF by default)
    ENABLE_SEMANTIC_VALIDATION = False  # Set to True to enable embedding-based hallucination detection

    # HitL approve sets status=approved before report finishes; report then writes the memo.
    # Only skip when already published or there is nothing to review.
    if state.get("memo_confirmed"):
        return {"traces": [{"node": "memo_gate", "skipped": "confirmed"}]}
    if not has_draft and status not in {"draft", "approved", "revising"}:
        return {"traces": [{"node": "memo_gate", "skipped": "no_draft"}]}

    # Check memo quality for automatic regeneration triggers
    body_markdown = report.get("body_markdown") or ""
    evidence = state.get("retrieved") or state.get("evidence") or []
    
    # NEW: Get coverage for production-aware quality check (retrieval vs generation issues)
    critic = state.get("critic") or {}
    coverage = critic.get("coverage") or {}
    
    # Citation stacking / source saturation are pure marker-placement issues —
    # fix them deterministically (free, always succeeds) before deciding
    # whether the expensive LLM regenerate path is even still needed.
    body_markdown, declutter_n = declutter_citations(body_markdown)
    if declutter_n:
        report = {**report, "body_markdown": body_markdown}
        event("memo_gate_citations_decluttered", markers_changed=declutter_n)
    
    # NEW: Pass coverage to detect retrieval vs generation issues
    quality_check = check_memo_quality(body_markdown, evidence=evidence, coverage=coverage)
    
    # NEW (P3): Tier-C structural validation (programmatic checks for prompt-only rules)
    # Extract dimension names from dossier for per-dimension check
    dossier = state.get("critic", {}).get("coverage", {}).get("dossier") or []
    required_dimensions = [d.get("label") for d in dossier if d.get("label")] if dossier else None
    
    structure_validation = validate_memo_structure(
        body_markdown,
        required_dimensions=required_dimensions,
        citation_stacking_threshold=3.0
    )
    
    # Integrate structural errors into quality_check issues
    if not structure_validation["overall_pass"]:
        quality_check["issues"].extend(structure_validation["errors"])
        quality_check["should_regenerate"] = True
        event("memo_gate_structure_violations", 
              error_count=structure_validation["error_count"],
              errors=structure_validation["errors"])
    
    # Log structural warnings (don't block, but track)
    if structure_validation["warning_count"] > 0:
        event("memo_gate_structure_warnings",
              warning_count=structure_validation["warning_count"],
              warnings=structure_validation["warnings"])
    
    # NEW (Issue #4): Optional semantic validation (embedding-based hallucination detection)
    # HIGH COMPUTE COST - Only enable if needed for critical quality assurance
    if ENABLE_SEMANTIC_VALIDATION and body_markdown and evidence:
        try:
            from app.maintenance.semantic_validation import validate_memo_semantics, should_trigger_regeneration
            # NOTE: This requires embedding model to be available
            # For now, we skip if not configured to avoid breaking existing flow
            embedding_model = None  # TODO: Wire to actual embedding model from config
            
            if embedding_model:
                semantic_result = validate_memo_semantics(
                    memo_markdown=body_markdown,
                    evidence_pool=evidence,
                    embedding_model=embedding_model,
                    threshold=0.70  # From semantic_validation.py
                )
                
                if should_trigger_regeneration(semantic_result):
                    quality_check["issues"].append(
                        f"Semantic validation: {semantic_result['critical_issues']} claims "
                        f"not supported by sources (hallucination rate: {semantic_result['hallucination_rate_estimate']:.0%})"
                    )
                    quality_check["should_regenerate"] = True
                    event("memo_gate_semantic_violations",
                          critical_issues=semantic_result["critical_issues"],
                          hallucination_rate=semantic_result["hallucination_rate_estimate"])
        except Exception as exc:
            # Don't fail the gate if semantic validation fails
            event("memo_gate_semantic_validation_error", error=str(exc)[:200])

    # Hard gate: missing mandatory primaries (Hu / Dettmers) must not soft-publish green.
    _mand = _mandatory_hard_fail_patch(state, report)
    if _mand and not _mand.get("_mandatory_block"):
        return _mand
    if _mand and _mand.get("_mandatory_block"):
        quality_check["issues"].append(
            "Mandatory primary sources missing (e.g. Hu 2106.09685 / Dettmers 2305.14314); "
            "do not treat this memo as green until they are cited."
        )
        quality_check["should_regenerate"] = True
        quality_check["hard_fail"] = True
        quality_check["should_block_publish"] = True
        event("memo_gate_mandatory_hard_fail", missing=_mand.get("missing_mandatory"))

    # Track regeneration attempts to prevent infinite loops
    quality_regen_count = int(state.get("quality_regeneration_count") or 0)
    MAX_QUALITY_REGENERATIONS = QualityThresholds.MAX_QUALITY_REGENERATIONS

    # If quality issues detected AND under limit, trigger report rewrite from notes (not new search)
    if quality_check["should_regenerate"] and quality_regen_count < MAX_QUALITY_REGENERATIONS:
        from app.domain.schema import AgentName, SubQuery
        
        issue_summary = "; ".join(quality_check["issues"][:3])
        event("memo_gate_quality_regenerate", issues=issue_summary, attempt=quality_regen_count + 1)
        
        # Trigger report rewrite with quality issues as feedback
        # Do NOT create new search - rewrite from existing dimension-filtered notes
        # The report node will regenerate using the same dossier + quality feedback
        return {
            "status": "revising_quality",
            "memo_confirmed": False,
            "quality_gate_issues": quality_check["issues"],
            "quality_regeneration_count": quality_regen_count + 1,
            "traces": [{
                "node": "memo_gate",
                "action": "quality_regenerate",
                "issues": quality_check["issues"],
                "attempt": quality_regen_count + 1,
                "will_rewrite_report": True,
            }],
        }
    elif quality_check["should_regenerate"] and quality_regen_count >= MAX_QUALITY_REGENERATIONS:
        # Hit regeneration limit - force publish with warning
        event("memo_gate_quality_limit_reached", attempts=quality_regen_count, remaining_issues=len(quality_check["issues"]))
        # Fall through to normal gate flow (will show to user or auto-publish)

    report = _prefer_prior_if_better(state, report)
    if report.get("metrics", {}).get("augment_kept_prior"):
        quality_check = check_memo_quality(report.get("body_markdown") or "", evidence=evidence, coverage=coverage)

    # TEMPORARILY DISABLED HITL timeout tracking (causing runtime error)
    payload = pythonize(
        {
            "type": "memo_draft",
            "title": "Memo draft",
            "subtitle": "Publish as-is or send back to the critic for another evidence pass.",
            "query": state.get("query"),
            "report": {
                "title": report.get("title"),
                "executive_summary": report.get("executive_summary"),
                "body_markdown": report.get("body_markdown"),
                "decision_rule": report.get("decision_rule"),
            },
            "critic": state.get("critic"),
            "budget": state.get("budget"),
            "coverage_gate": (state.get("critic") or {}).get("coverage_gate") or {},
            "coverage_slots": ((state.get("critic") or {}).get("coverage") or {}).get("slots") or [],
            "gate_message": ((state.get("critic") or {}).get("coverage_gate") or {}).get("message") or "",
            "gate_reason": (report.get("metrics") or {}).get("gate_reason")
            or ((state.get("critic") or {}).get("coverage_gate") or {}).get("gate_reason"),
            "synthesis_status": (report.get("metrics") or {}).get("synthesis_status"),
            "quality_check": quality_check,
        }
    )
    event("memo_gate_interrupt")
    decision = interrupt(payload)
    if isinstance(decision, str):
        decision = {"action": decision}
    
    action = (decision or {}).get("action", "publish")
    notes = str((decision or {}).get("notes") or "").strip()
    extra = (decision or {}).get("extra_questions") or []

    if action == "revise_critic":
        from app.domain.schema import AgentName, SubQuery

        followups = []
        prompts = extra if isinstance(extra, list) and extra else ([notes] if notes else [state.get("query") or ""])
        for q in prompts:
            if not str(q).strip():
                continue
            followups.append(
                dump(SubQuery(agent=AgentName.SEARCH, question=str(q), rationale="Memo draft revision"))
            )
        event("memo_gate_revise", followups=len(followups))
        return {
            "status": "revising",
            "memo_confirmed": False,
            "followups": followups,
            "human_decision": decision or {"action": "revise_critic"},
            "traces": [{"node": "memo_gate", "action": "revise_critic", "followups": len(followups)}],
        }

    # Human chose publish; if mandatory hard-fail remains, record it (not a silent green).
    if action == "publish":
        audit = ((report.get("metrics") or {}).get("constraint_audit") or {})
        if audit.get("hard_fail") or audit.get("should_block_publish") or (quality_check or {}).get("hard_fail"):
            report = {
                **report,
                "metrics": {
                    **(report.get("metrics") or {}),
                    "hard_fail": True,
                    "published_despite_mandatory_gap": True,
                    "should_block_publish": True,
                },
            }
            event("memo_gate_publish_despite_mandatory_hard_fail")

    # `report` here already carries the decluttered body_markdown from above —
    # persist and publish that version, not state's original.
    stored = _persist_knowledge(state, report)
    _log_run_token_summary(state, report)
    patch: dict = {
        "status": "completed",
        "memo_confirmed": True,
        "human_decision": decision or {"action": "publish"},
        "traces": [{"node": "memo_gate", "action": "publish"}],
        "report": report,
    }
    if stored:
        metrics = dict(report.get("metrics") or {})
        metrics["knowledge_id"] = stored.get("id")
        metrics["knowledge_version"] = stored.get("version")
        metrics["knowledge_sources"] = len(stored.get("citations") or [])
        patch["report"] = {**report, "metrics": metrics}
    return patch


def memo_gate_node_auto(state: ResearchState) -> dict:
    """Auto-publish path with quality checks - must match HITL standards."""
    from app.domain.memo_quality import check_memo_quality, declutter_citations

    report = state.get("report") or {}
    body_markdown = report.get("body_markdown") or ""
    evidence = state.get("retrieved") or state.get("evidence") or []
    
    # NEW: Get coverage for production-aware quality check
    critic = state.get("critic") or {}
    coverage = critic.get("coverage") or {}

    # Same free, deterministic pre-pass as the HITL path.
    if body_markdown:
        body_markdown, declutter_n = declutter_citations(body_markdown)
        if declutter_n:
            report = {**report, "body_markdown": body_markdown}
            event("memo_gate_citations_decluttered", markers_changed=declutter_n)

    # Run same quality check as HITL path (with coverage for retrieval awareness)
    if body_markdown:
        quality_check = check_memo_quality(body_markdown, evidence=evidence, coverage=coverage)
        
        _mand = _mandatory_hard_fail_patch(state, report)
        if _mand and not _mand.get("_mandatory_block"):
            return _mand
        if _mand and _mand.get("_mandatory_block"):
            quality_check["issues"].append(
                "Mandatory primary sources missing; blocking auto-publish."
            )
            quality_check["should_regenerate"] = True
            quality_check["hard_fail"] = True
            quality_check["should_block_publish"] = True
            event("memo_gate_auto_mandatory_block", missing=_mand.get("missing_mandatory"))
            return {
                "status": "draft",
                "memo_confirmed": False,
                "report": {
                    **report,
                    "metrics": {
                        **(report.get("metrics") or {}),
                        "hard_fail": True,
                        "should_block_publish": True,
                        "publish_blocked_reason": "mandatory_sources_missing",
                    },
                },
                "traces": [{
                    "node": "memo_gate_auto",
                    "action": "block_publish_mandatory",
                    "missing": _mand.get("missing_mandatory"),
                }],
            }

        
        # If quality issues detected, trigger rewrite from notes (not new search)
        quality_regen_count_auto = int(state.get("quality_regeneration_count") or 0)
        MAX_QUALITY_REGENERATIONS = QualityThresholds.MAX_QUALITY_REGENERATIONS
        
        if quality_check["should_regenerate"] and quality_regen_count_auto < MAX_QUALITY_REGENERATIONS:
            event("memo_gate_auto_quality_fail", issues="; ".join(quality_check["issues"][:3]), attempt=quality_regen_count_auto + 1)
            
            # Trigger report rewrite with quality issues as feedback
            # Do NOT create new search - rewrite from existing dimension-filtered notes
            return {
                "status": "revising_quality",
                "memo_confirmed": False,
                "quality_gate_issues": quality_check["issues"],
                "quality_regeneration_count": quality_regen_count_auto + 1,
                "traces": [{
                    "node": "memo_gate_auto",
                    "action": "quality_regenerate",
                    "issues": quality_check["issues"],
                    "attempt": quality_regen_count_auto + 1,
                    "will_rewrite": True,
                }],
            }
        elif quality_check["should_regenerate"] and quality_regen_count_auto >= MAX_QUALITY_REGENERATIONS:
            event("memo_gate_auto_quality_limit_reached", attempts=quality_regen_count_auto, remaining_issues=len(quality_check["issues"]))
            # Force approve after limit
            pass  # Fall through to final return

    report = _prefer_prior_if_better(state, report)
    stored = _persist_knowledge(state, report)
    _log_run_token_summary(state, report)
    patch: dict = {
        "status": "completed",
        "memo_confirmed": True,
        "traces": [{"node": "memo_gate", "skipped": "auto"}],
    }
    if report:
        patch["report"] = report
    if stored and report:
        metrics = dict(report.get("metrics") or {})
        metrics["knowledge_id"] = stored.get("id")
        metrics["knowledge_version"] = stored.get("version")
        patch["report"] = {**report, "metrics": metrics}
    return patch


def _prefer_prior_if_better(state: ResearchState, report: dict) -> dict:
    """Augment mode rewrites the whole memo from a shrunken evidence set
    (see planner._followups_from_prior / knowledge.lookup) — nothing forces
    that rewrite to be at least as good as the memo already on file, and the
    writer only ever sees a 1200-char summary of the old one, not its body.
    Compare the two on the same depth score `save_answer` itself uses, and
    serve the prior verbatim when the fresh attempt scored lower, so augment
    can only hold steady or improve, never regress the user's memo.
    """
    if state.get("reuse_mode") != "augment":
        return report
    prior = state.get("prior_knowledge") or {}
    prior_id = prior.get("id")
    if not prior_id:
        return report
    fresh_score = depth_of(report.get("metrics") or {}, (state.get("critic") or {}).get("coverage"))
    prior_score = int(prior.get("depth_score") or 0)
    if fresh_score >= prior_score:
        return report
    event(
        "memo_gate_augment_kept_prior",
        fresh_score=fresh_score,
        prior_score=prior_score,
        knowledge_id=prior_id,
    )
    metrics = dict(report.get("metrics") or {})
    metrics.update(
        knowledge_id=prior_id,
        knowledge_version=prior.get("version"),
        augment_kept_prior=True,
        augment_fresh_score=fresh_score,
        depth_score=prior_score,
    )
    return {
        **report,
        "title": prior.get("title") or report.get("title"),
        "executive_summary": prior.get("executive_summary") or report.get("executive_summary"),
        "body_markdown": prior.get("body_markdown") or report.get("body_markdown"),
        "decision_rule": prior.get("decision_rule") or report.get("decision_rule"),
        "open_questions": prior.get("open_questions") or report.get("open_questions"),
        "limitations": prior.get("limitations") or report.get("limitations"),
        "claims": prior.get("claims") or report.get("claims"),
        "citations": prior.get("citations") or report.get("citations"),
        "metrics": metrics,
    }


def _log_run_token_summary(state: ResearchState, report: dict) -> None:
    """One clean total per run — the per-call llm_call_ok lines in
    llm/client.py give the detail, this gives the number without having to
    grep-and-sum them by hand (see: manual ~105k-token estimate for a run
    whose logs had already rotated away, later found ~15% high once actually
    measured). budget.used_tokens now accumulates from every LLM-calling
    node (briefing, planner, critic, retrieve/rerank, report), not just the
    two that used to update it."""
    budget = state.get("budget") or {}
    metrics = (report or {}).get("metrics") or {}
    event(
        "run_token_summary",
        thread_id=state.get("thread_id"),
        depth=(state.get("brief") or {}).get("depth"),
        used_tokens=budget.get("used_tokens"),
        usd_est=metrics.get("usd_est"),
        used_tool_calls=budget.get("used_tool_calls"),
        used_enrich_calls=budget.get("used_enrich_calls"),
        used_retrieval_calls=budget.get("used_retrieval_calls"),
        iterations=budget.get("iterations"),
    )


def _persist_knowledge(state: ResearchState, report_override: dict | None = None) -> dict | None:
    report = report_override if report_override is not None else (state.get("report") or {})
    if report.get("metrics", {}).get("knowledge_id"):
        kid = report["metrics"]["knowledge_id"]
        mark_reused(str(kid))
        return None
    retrieved = state.get("retrieved") or state.get("evidence") or []
    citations = report.get("citations") or []
    grounded = bool(citations) and any(
        isinstance(c, dict) and c.get("url") for c in citations
    )
    if not grounded:
        return None
    critic = state.get("critic") or {}
    stored = save_answer(
        state.get("query") or "",
        dump(report),
        critic.get("coverage") or {},
        prior_id=state.get("prior_knowledge_id"),
        org_id=state.get("org_id") or None,
        user_id=state.get("user_id") or None,
        thread_id=state.get("thread_id") or None,
    )
    if stored and state.get("prior_knowledge_id"):
        mark_reused(str(state["prior_knowledge_id"]))
    return stored
