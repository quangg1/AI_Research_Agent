from __future__ import annotations

from langgraph.types import interrupt

from app.domain.knowledge import mark_reused, save_answer
from app.graph.serde import dump, pythonize
from app.graph.state import ResearchState
from app.observability.logging import event


def memo_gate_node(state: ResearchState) -> dict:
    """Review the written memo draft before publishing to the user."""
    from app.domain.memo_quality import check_memo_quality
    
    report = state.get("report") or {}
    has_draft = bool((report.get("body_markdown") or "").strip())
    status = str(state.get("status") or "")

    # HitL approve sets status=approved before report finishes; report then writes the memo.
    # Only skip when already published or there is nothing to review.
    if state.get("memo_confirmed"):
        return {"traces": [{"node": "memo_gate", "skipped": "confirmed"}]}
    if not has_draft and status not in {"draft", "approved", "revising"}:
        return {"traces": [{"node": "memo_gate", "skipped": "no_draft"}]}

    # Check memo quality for automatic regeneration triggers
    body_markdown = report.get("body_markdown") or ""
    evidence = state.get("retrieved") or state.get("evidence") or []
    quality_check = check_memo_quality(body_markdown, evidence=evidence)
    
    # If quality issues detected, trigger report rewrite from notes (not new search)
    if quality_check["should_regenerate"]:
        from app.domain.schema import AgentName, SubQuery
        
        issue_summary = "; ".join(quality_check["issues"][:3])
        event("memo_gate_quality_regenerate", issues=issue_summary)
        
        # Trigger report rewrite with quality issues as feedback
        # Do NOT create new search - rewrite from existing dimension-filtered notes
        # The report node will regenerate using the same dossier + quality feedback
        return {
            "status": "revising_quality",
            "memo_confirmed": False,
            "quality_gate_issues": quality_check["issues"],
            "traces": [{
                "node": "memo_gate",
                "action": "quality_regenerate",
                "issues": quality_check["issues"],
                "will_rewrite_report": True,
            }],
        }

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

    stored = _persist_knowledge(state)
    patch: dict = {
        "status": "completed",
        "memo_confirmed": True,
        "human_decision": decision or {"action": "publish"},
        "traces": [{"node": "memo_gate", "action": "publish"}],
    }
    if stored:
        report = dict(state.get("report") or {})
        metrics = dict(report.get("metrics") or {})
        metrics["knowledge_id"] = stored.get("id")
        metrics["knowledge_version"] = stored.get("version")
        metrics["knowledge_sources"] = len(stored.get("citations") or [])
        report["metrics"] = metrics
        patch["report"] = report
    return patch


def memo_gate_node_auto(state: ResearchState) -> dict:
    """Auto-publish path with quality checks - must match HITL standards."""
    from app.domain.memo_quality import check_memo_quality
    
    report = state.get("report") or {}
    body_markdown = report.get("body_markdown") or ""
    evidence = state.get("retrieved") or state.get("evidence") or []
    
    # Run same quality check as HITL path
    if body_markdown:
        quality_check = check_memo_quality(body_markdown, evidence=evidence)
        
        # If quality issues detected, trigger rewrite from notes (not new search)
        if quality_check["should_regenerate"]:
            event("memo_gate_auto_quality_fail", issues="; ".join(quality_check["issues"][:3]))
            
            # Trigger report rewrite with quality issues as feedback
            # Do NOT create new search - rewrite from existing dimension-filtered notes
            return {
                "status": "revising_quality",
                "memo_confirmed": False,
                "quality_gate_issues": quality_check["issues"],
                "traces": [{
                    "node": "memo_gate_auto",
                    "action": "quality_regenerate",
                    "issues": quality_check["issues"],
                    "will_rewrite": True,
                }],
            }
    
    stored = _persist_knowledge(state)
    patch: dict = {
        "status": "completed",
        "memo_confirmed": True,
        "traces": [{"node": "memo_gate", "skipped": "auto"}],
    }
    if stored and state.get("report"):
        report = dict(state["report"])
        metrics = dict(report.get("metrics") or {})
        metrics["knowledge_id"] = stored.get("id")
        metrics["knowledge_version"] = stored.get("version")
        report["metrics"] = metrics
        patch["report"] = report
    return patch


def _persist_knowledge(state: ResearchState) -> dict | None:
    report = state.get("report") or {}
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
    )
    if stored and state.get("prior_knowledge_id"):
        mark_reused(str(state["prior_knowledge_id"]))
    return stored
