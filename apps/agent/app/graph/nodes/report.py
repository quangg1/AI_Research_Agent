from __future__ import annotations

import re

import asyncio

from app.domain.citations import annotate_inline_citation_tiers, bind_markdown_to_ledger, build_ledger
from app.domain.coverage import build_evidence_dossier
from app.domain.coverage_gate import gate_from_critic
from app.domain.grounding import verify_claims
from app.domain.knowledge import mark_reused, save_answer
from app.domain.research_intent import user_goal
from app.domain.schema import Claim, CitationRef, Report
from app.domain.textutil import distinctive_terms
from app.graph.serde import dump
from app.graph.state import ResearchState, budget_from
from app.llm.client import CreditsExhaustedError, llm
from app.llm.roles import use_role_model
from app.observability.logging import event
from app.report.compose import (
    GRAPH_VERSION,
    compose_report,
    compose_terminal_synthesis,
    memo_is_user_clean,
)
from app.domain.adversarial import method_notes_for_writer
from app.report.adaptive_depth import calculate_adaptive_target, format_writer_guidance, should_use_graceful_degradation
from app.report.deep_write import (
    claims_prompt,
    compress_max_tokens,
    compress_prompt,
    compress_system,
    filter_dossier_for_writer,
    format_research_notes,
    notes_max_chars,
    parse_report_markdown,
    prioritize_dossier_for_writer,
    report_max_tokens,
    should_skip_llm_compress,
    word_count,
    word_target,
    writer_prompt,
    writer_system,
)


async def report_node(state: ResearchState) -> dict:
    return await asyncio.to_thread(_report_sync, state)


def _report_sync(state: ResearchState) -> dict:
    if state.get("reuse_mode") == "cached" and state.get("prior_knowledge"):
        return _reuse_stored_answer(state)
    
    # Quality regeneration path: rewrite from existing notes with quality feedback
    if state.get("status") == "revising_quality" and state.get("quality_gate_issues"):
        return _regenerate_for_quality(state)
    
    terminal_status = state.get("status") if state.get("status") in {"out_of_scope", "cancelled"} else "draft"
    retrieved = state.get("retrieved") or state.get("evidence") or []
    critic = state.get("critic") or {}
    budget = budget_from(state)
    citations = [c.model_dump(mode="json") for c in build_ledger(retrieved)] or list(state.get("citations") or [])
    metrics = _metrics(state, budget)
    metrics["query_type"] = state.get("query_type") or (state.get("brief") or {}).get("query_type")
    seed_claims = None
    if state.get("claims"):
        try:
            seed_claims = [Claim.model_validate(c) for c in state["claims"]]
        except Exception:
            seed_claims = None
    terminal_followups = state.get("terminal_followups") or []
    synthesis_status = _synthesis_status(state, critic)
    report = _llm_report(
        state,
        retrieved,
        citations,
        metrics,
        seed_claims,
        terminal_followups=terminal_followups,
        synthesis_status=synthesis_status,
    )
    if report is None:
        report = compose_terminal_synthesis(
            query=state.get("query") or "",
            evidence=retrieved,
            critic=critic,
            plan=state.get("plan") or {},
            brief=state.get("brief") or {},
            budget=dump(budget),
            llm_mode=_effective_llm_mode(synthesis_status),
            citations=citations,
            claims=seed_claims,
            metrics=metrics,
            terminal_followups=terminal_followups,
        )
    if llm.last_tokens:
        budget.used_tokens += llm.last_tokens
        report.metrics["tokens"] = budget.used_tokens
        report.metrics["usd_est"] = round(budget.used_tokens / 1_000_000 * 0.40, 4)
    gate = gate_from_critic(critic) or gate_from_critic(report.metrics.get("critic") or {})
    if gate:
        report.metrics["coverage_gate"] = gate
        report.metrics["gate_reason"] = gate.get("gate_reason")
    if synthesis_status == "terminal_fallback":
        report.metrics["synthesis_status"] = "terminal_fallback"
        report.body_markdown = _ensure_budget_gap_notice(report.body_markdown or "", synthesis_status)
    report.citations = [CitationRef.model_validate(c) for c in citations]
    report.body_markdown = bind_markdown_to_ledger(report.body_markdown or "", citations)
    from app.domain.fact_lite import verify_memo_citations

    fact = verify_memo_citations(report.body_markdown or "", citations, retrieved)
    report.metrics["fact_lite"] = fact
    report.decision_rule = _sanitize_decision_rule(state.get("query") or "", report.decision_rule or "", citations, critic)
    integrity = _apply_integrity(report, citations, critic, retrieved, query=state.get("query") or "")
    report.body_markdown = integrity["body_markdown"]
    report.decision_rule = integrity["decision_rule"]
    report.at_a_glance = integrity["at_a_glance"]
    report.limitations = integrity["limitations"]
    report.metrics = {**report.metrics, **integrity["metrics_patch"]}
    report.body_markdown = annotate_inline_citation_tiers(report.body_markdown or "", citations)
    report.decision_rule = annotate_inline_citation_tiers(report.decision_rule or "", citations)
    if "## Decision rule" in (report.body_markdown or "") and report.decision_rule:
        head, _, rest = report.body_markdown.partition("## Decision rule")
        after = rest.split("\n## ", 1)
        tail = ("\n## " + after[1]) if len(after) > 1 else ""
        report.body_markdown = f"{head}## Decision rule\n\n{report.decision_rule}\n{tail}".rstrip() + "\n"
    verified = verify_claims(report.claims, retrieved)
    from app.domain.coverage import GENERIC_CLAIM_RE

    cleaned = []
    for c in verified:
        text = f"{c.get('text', '')} {c.get('quote', '')}"
        if GENERIC_CLAIM_RE.search(text) and not c.get("slot_id"):
            continue
        cleaned.append(c)
    if seed_claims and len(cleaned) < len(seed_claims):
        report.claims = seed_claims
    else:
        report.claims = [Claim.model_validate(c) for c in (cleaned or verified)]
    from app.domain.claim_quote_verify import enrich_claim_quotes_llm

    report.claims, quote_stats = enrich_claim_quotes_llm(report.claims, retrieved, citations)
    report.metrics["claim_quote_verify"] = quote_stats
    for claim in report.claims:
        allowed = {c.get("url") for c in citations}
        if claim.url and claim.url not in allowed:
            claim.url = next((c.get("url") or "" for c in citations if c.get("evidence_id") in (claim.support_ids or [])), "")
    _attach_evidence_graph(state, report, retrieved, citations, refetch=True)

    from app.domain.report_integrity import (
        build_integrity_reloop_followups,
        integrity_severity,
    )

    severity = integrity_severity(integrity["integrity_issues"], report.body_markdown or "")
    report.metrics["integrity_severity"] = severity
    retries = int(state.get("integrity_retries") or 0)
    if (
        severity == "critical"
        and retries < 1
        and budget.remaining_iterations > 0
        and budget.remaining_calls >= 2
        and terminal_status == "draft"
    ):
        followups = build_integrity_reloop_followups(state.get("query") or "", integrity["integrity_issues"])
        event("report_integrity_reloop", severity=severity, retries=retries + 1)
        return {
            "report": dump(report),
            "claims": [dump(c) for c in report.claims],
            "budget": dump(budget),
            "llm_mode": _effective_llm_mode(report.metrics.get("synthesis_status") or synthesis_status),
            "status": "integrity_research",
            "integrity_retries": retries + 1,
            "followups": followups,
            "plan_confirmed": True,
            "traces": [
                {
                    "node": "report",
                    "action": "integrity_reloop",
                    "severity": severity,
                    "retries": retries + 1,
                    "claims": len(report.claims),
                    "mode": llm.mode,
                    "version": GRAPH_VERSION,
                    "used_tokens_delta": llm.last_tokens or 0,
                }
            ],
        }

    event(
        "report",
        claims=len(report.claims),
        mode=llm.mode,
        version=GRAPH_VERSION,
        synthesis=report.metrics.get("synthesis_status"),
        knowledge_version=report.metrics.get("knowledge_version"),
    )
    return {
        "report": dump(report),
        "claims": [dump(c) for c in report.claims],
        "budget": dump(budget),
        "llm_mode": _effective_llm_mode(report.metrics.get("synthesis_status") or synthesis_status),
        # Report generation is a terminal presentation step. It must not erase
        # a routing/cancellation decision made earlier in the graph.
        "status": terminal_status,
        "traces": [
            {
                "node": "report",
                "claims": len(report.claims),
                "mode": llm.mode,
                "version": GRAPH_VERSION,
                "synthesis": report.metrics.get("synthesis_status"),
                "used_tokens_delta": llm.last_tokens or 0,
            }
        ],
    }


def _reuse_stored_answer(state: ResearchState) -> dict:
    """Serve a near-identical past question from the knowledge store, no API calls."""
    record = state.get("prior_knowledge") or {}
    budget = budget_from(state)
    citations = [
        {
            "n": i + 1,
            "evidence_id": c.get("evidence_id") or f"know-{i}",
            "title": c.get("title") or "",
            "url": c.get("url") or "",
            "quote": c.get("quote") or "",
            "tier": c.get("tier") or "",
            "host": c.get("host") or "",
        }
        for i, c in enumerate(record.get("citations") or [])
    ]
    claims = [
        Claim(
            id=f"C{i + 1}",
            text=c.get("text") or "",
            quote=c.get("quote") or "",
            url=c.get("url") or "",
            tier=c.get("tier") or "",
            confidence=float(c.get("confidence") or 0.6),
            grounded=bool(c.get("url")),
        )
        for i, c in enumerate(record.get("claims") or [])
        if (c.get("text") or "").strip()
    ]
    report = Report(
        title=record.get("title") or user_goal(state.get("query") or ""),
        executive_summary=record.get("executive_summary") or "",
        body_markdown=record.get("body_markdown") or "",
        claims=claims,
        citations=[CitationRef.model_validate(c) for c in citations],
        open_questions=list(record.get("open_questions") or []),
        limitations=list(record.get("limitations") or []),
        decision_rule=record.get("decision_rule") or "",
        method_notes=[
            f"Served from the knowledge store (version {record.get('version')}).",
            "No new retrieval or generation calls were made for this answer.",
        ],
        metrics={
            "version": GRAPH_VERSION,
            "synthesis_status": "knowledge_reuse",
            "reuse_mode": "cached",
            "reuse_similarity": state.get("reuse_similarity"),
            "knowledge_id": record.get("id"),
            "knowledge_version": record.get("version"),
            "knowledge_age_days": state.get("reuse_age_days"),
            "sources": len(citations),
            "unique_sources": len(citations),
            "depth_score": record.get("depth_score"),
            "must_answer": record.get("slots") or [],
            "tool_calls": 0,
            "iterations": budget.iterations,
        },
    )
    mark_reused(record.get("id") or "")
    _attach_evidence_graph(state, report, [], citations, refetch=False)
    event("report_knowledge_reuse", knowledge_id=record.get("id"), similarity=state.get("reuse_similarity"))
    return {
        "report": dump(report),
        "claims": [dump(c) for c in report.claims],
        "citations": citations,
        "budget": dump(budget),
        "llm_mode": "knowledge_reuse",
        "status": "completed",
        "traces": [
            {
                "node": "report",
                "reuse": "cached",
                "knowledge_id": record.get("id"),
                "similarity": state.get("reuse_similarity"),
            }
        ],
    }


def _synthesis_status(state: ResearchState, critic: dict) -> str:
    if not llm.available:
        return "heuristic_no_key"
    budget = budget_from(state)
    gate = gate_from_critic(critic)
    gate_reason = gate.get("gate_reason") or critic.get("gate_reason")
    if gate_reason == "insufficient_budget":
        return "terminal_fallback"
    if critic.get("status") == "insufficient" and budget.remaining_iterations <= 0:
        return "terminal_fallback"
    return "gemini_pending"


def _ensure_budget_gap_notice(body: str, synthesis_status: str) -> str:
    if synthesis_status != "terminal_fallback":
        return body
    marker = "could not be verified within the research budget"
    if marker in (body or "").lower():
        return body
    notice = (
        "> **Note:** Some dimensions could not be verified within the research budget. "
        "The analysis below reflects the strongest evidence collected; open items are listed "
        "under Limitations.\n"
    )
    lines = (body or "").splitlines()
    if not lines:
        return notice.strip()
    out: list[str] = []
    inserted = False
    for i, line in enumerate(lines):
        out.append(line)
        if not inserted and line.startswith("## Executive summary"):
            if i + 1 < len(lines) and lines[i + 1].strip():
                out.append("")
                out.append(notice.rstrip())
                inserted = True
    if inserted:
        return "\n".join(out)
    return f"{notice}{body}"


def _effective_llm_mode(synthesis_status: str) -> str:
    if synthesis_status in {"heuristic_no_key", "heuristic"}:
        return "heuristic"
    if synthesis_status == "gemini_failed_fallback":
        return "gemini_failed_fallback"
    if synthesis_status == "terminal_fallback":
        return llm.mode if llm.available else "heuristic"
    return llm.mode


def _metrics(state: ResearchState, budget) -> dict:
    critic = state.get("critic") or {}
    cov = critic.get("coverage") or {}
    depth = critic.get("depth_score") or {}
    gate = gate_from_critic(critic)
    return {
        "sources": len(state.get("retrieved") or state.get("evidence") or []),
        "iterations": budget.iterations,
        "tool_calls": budget.used_tool_calls,
        "used_retrieval_calls": budget.used_retrieval_calls,
        "max_retrieval_calls": budget.max_retrieval_calls,
        "used_enrich_calls": budget.used_enrich_calls,
        "max_enrich_calls": budget.max_enrich_calls,
        "tokens": budget.used_tokens,
        "usd_est": round(budget.used_tokens / 1_000_000 * 0.40, 4),
        "version": GRAPH_VERSION,
        "query_type": (state.get("plan") or {}).get("query_type") or state.get("query_type"),
        "coverage_ratio": cov.get("ratio"),
        "must_answer_fraction": cov.get("must_answer_fraction") or (depth.get("must_answer") or {}).get("fraction"),
        "critical_fraction": cov.get("critical_fraction") or (depth.get("critical") or {}).get("fraction"),
        "depth_score": depth.get("score"),
        "depth_label": depth.get("label"),
        "confidence_breakdown": depth.get("breakdown") or {},
        "has_implementation": cov.get("has_implementation"),
        "unique_sources": cov.get("unique_sources"),
        "critical_gaps": [g.get("id") for g in (cov.get("critical_gaps") or [])],
        "must_answer": cov.get("slots") or [],
        "critic_status": critic.get("status"),
        "gate_reason": gate.get("gate_reason") or critic.get("gate_reason"),
        "coverage_gate": gate or critic.get("coverage_gate") or {},
        "quality": depth,
    }



def _llm_memo_worth_keeping(markdown: str) -> bool:
    """True when an LLM draft is clearly better than compose fallback."""
    text = (markdown or "").strip()
    if word_count(text) < 400:
        return False
    if not re.search(r"^##\s+Executive summary\s*$", text, re.I | re.M):
        return False
    if not re.search(r"^##\s+Detailed analysis\s*$", text, re.I | re.M):
        return False
    compose_markers = (
        "taken together,",
        "agree on this dimension, so it can be treated as established",
        "evidence reaches ",
    )
    low = text.lower()
    if sum(1 for m in compose_markers if m in low) >= 2:
        return False
    return True


def _prefer_llm_or_compose(
    *,
    llm_markdown: str,
    compose_report_obj,
    reason: str,
    metrics: dict,
):
    """Keep substantial LLM drafts instead of silently replacing with compose."""
    if _llm_memo_worth_keeping(llm_markdown):
        notice = (
            "> **Note:** Writer QA flagged this memo ("
            + reason
            + "); kept the LLM draft instead of replacing it with the heuristic template."
        )
        body = llm_markdown
        if reason not in body and "kept the LLM draft" not in body:
            lines = body.splitlines()
            out: list[str] = []
            inserted = False
            for i, line in enumerate(lines):
                out.append(line)
                if not inserted and line.startswith("## Executive summary"):
                    if i + 1 < len(lines) and lines[i + 1].strip():
                        out.append("")
                        out.append(notice)
                        inserted = True
            body = "\n".join(out) if inserted else (notice + "\n\n" + body)
        return compose_report_obj.model_copy(
            update={
                "body_markdown": body,
                "metrics": {
                    **(compose_report_obj.metrics or {}),
                    **metrics,
                    "synthesis_status": reason,
                    "kept_llm_despite_qa": True,
                    "writer": "markdown",
                    "word_count": word_count(body),
                },
            }
        )
    compose_report_obj.metrics = {
        **(compose_report_obj.metrics or {}),
        **metrics,
        "synthesis_status": reason,
    }
    return compose_report_obj


def _llm_report(
    state: ResearchState,
    evidence: list[dict],
    citations: list[dict],
    metrics: dict,
    seed_claims: list[Claim] | None = None,
    *,
    terminal_followups: list | None = None,
    synthesis_status: str = "gemini_pending",
) -> Report | None:
    critic = state.get("critic") or {}
    budget = budget_from(state)
    dossier = build_evidence_dossier(state.get("query") or "", evidence, critic.get("coverage") or {})
    terminal = synthesis_status == "terminal_fallback" or (
        (gate_from_critic(critic).get("gate_reason") or critic.get("gate_reason")) == "insufficient_budget"
    ) or (critic.get("status") == "insufficient" and budget.remaining_iterations <= 0)

    if not llm.available and not (getattr(llm, "_slots", None) or []):
        return compose_terminal_synthesis(
            query=state.get("query") or "",
            evidence=evidence,
            critic=critic,
            plan=state.get("plan") or {},
            brief=state.get("brief") or {},
            budget=state.get("budget") or {},
            llm_mode="heuristic_no_key",
            citations=citations,
            claims=seed_claims,
            metrics=metrics,
            terminal_followups=terminal_followups,
        )

    base = compose_report(
        query=state.get("query") or "",
        evidence=evidence,
        critic=critic,
        plan=state.get("plan") or {},
        brief=state.get("brief") or {},
        budget=state.get("budget") or {},
        llm_mode=llm.mode,
        citations=citations,
        claims=seed_claims,
        metrics=metrics,
        synthesis_status="terminal_fallback" if terminal else "heuristic_seed",
        terminal_followups=terminal_followups,
    )

    depth = (state.get("brief") or {}).get("depth") or "standard"
    
    # Calculate adaptive word target based on actual evidence
    coverage = critic.get("coverage") or {}
    adaptive_target = calculate_adaptive_target(dossier, coverage)
    min_words = adaptive_target["total_words"]
    adaptive_guidance = format_writer_guidance(adaptive_target)
    
    # P5: Graceful degradation - downgrade from 'deep' to 'standard' if evidence is sparse
    should_degrade, suggested_depth = should_use_graceful_degradation(adaptive_target)
    if should_degrade and depth == "deep":
        event("graceful_degradation_triggered",
            original_depth=depth,
            new_depth=suggested_depth,
            reason=f"Evidence too sparse for deep synthesis (target: {min_words} words, "
                   f"coverage: {adaptive_target['coverage_tier']}, "
                   f"evidence_count: {adaptive_target['evidence_count']})"
        )
        depth = suggested_depth
    
    # Log adaptive depth for transparency
    event("adaptive_depth_calculated",
        target_words=min_words,
        coverage_tier=adaptive_target["coverage_tier"],
        evidence_count=adaptive_target["evidence_count"],
        dimension_count=adaptive_target["dimension_count"],
        rationale=adaptive_target["rationale"],
        final_depth=depth,
        degraded=should_degrade
    )
    
    dossier = filter_dossier_for_writer(
        prioritize_dossier_for_writer(dossier),
        depth=depth,
    )
    notes, notes_prep = _compress_notes(state, dossier, citations)
    metrics = {**metrics, "notes_prep": notes_prep}
    dimension_list = "\n".join(
        f"- {d.get('label')} (status: {d.get('status')})" for d in dossier if d.get("label")
    ) or "- Answer the question directly"
    subjects = _comparison_subjects(state.get("query") or "", evidence)
    comparison_rule = (
        "Include a '## Comparison' table with one column per subject: "
        + ", ".join(subjects)
        + ". Every cell must come from a source that names that subject; otherwise write "
        "'Not established in collected sources'. Never repeat the same passage across columns.\n"
        if len(subjects) >= 2
        else "Omit the Comparison section — the question does not compare named subjects.\n"
    )
    prior_note = ""
    if state.get("prior_knowledge"):
        prior = state["prior_knowledge"]
        prior_note = (
            "A previous memo answered a closely related question. Extend and correct it rather than "
            "restarting; keep anything it established that the new evidence still supports.\n"
            f"Previous executive summary:\n{(prior.get('executive_summary') or '')[:1200]}\n\n"
        )
    method_block = method_notes_for_writer(
        user_goal(state.get("query") or ""),
        state.get("brief") or {},
        evidence,
        citations,
    )
    prompt = writer_prompt(
        query=user_goal(state.get("query") or ""),
        brief=state.get("brief") or {},
        notes=notes,
        citations=citations,
        min_words=min_words,
        comparison_rule=comparison_rule,
        prior_note=prior_note,
        dimension_list=dimension_list,
        method_block=method_block,
        adaptive_guidance=adaptive_guidance,
    )
    from app.report.race_write import (
        criteria_block,
        ensure_memo_depth,
        generate_sectionwise_memo,
        memo_looks_truncated,
        polish_citations,
        race_criteria,
        repair_truncation,
        rewrite_for_quality,
    )

    race = race_criteria(user_goal(state.get("query") or ""), state.get("brief") or {}, dossier)
    prompt_with_race = f"{prompt}\n\n{criteria_block(race)}"
    tok_total = report_max_tokens(depth)
    tok_front = int(tok_total * 0.55) if str(depth).lower() == "deep" else tok_total
    tok_back = max(8192, tok_total - tok_front)
    markdown, write_mode = generate_sectionwise_memo(
        prompt_with_race,
        query=user_goal(state.get("query") or ""),
        notes=notes,
        citations=citations,
        criteria=race,
        comparison_rule=comparison_rule,
        depth=depth,
        max_tokens_front=tok_front,
        max_tokens_back=tok_back,
    )
    if word_count(markdown) < max(200, int(min_words * 0.28)):
        markdown = _generate_report_markdown(prompt_with_race, max_tokens=report_max_tokens(depth))
        write_mode = "single_retry"
    repaired = False
    if memo_looks_truncated(markdown):
        markdown, repaired = repair_truncation(
            markdown,
            query=user_goal(state.get("query") or ""),
            notes=notes,
            criteria=race,
            max_tokens=max(4096, report_max_tokens(depth) // 2),
        )
    markdown = polish_citations(markdown, citations=citations, evidence=evidence)
    expanded = False
    expand_passes = 0
    if word_count(markdown) < int(min_words * 0.92):
        markdown, expand_passes = ensure_memo_depth(
            markdown,
            query=user_goal(state.get("query") or ""),
            notes=notes,
            criteria=race,
            min_words=min_words,
            max_tokens=max(8192, report_max_tokens(depth) // 2),
            depth=depth,
            max_passes=2,
        )
        expanded = expand_passes > 0
        markdown = polish_citations(markdown, citations=citations, evidence=evidence)
    if memo_looks_truncated(markdown):
        failed = compose_terminal_synthesis(
            query=state.get("query") or "",
            evidence=evidence,
            critic=critic,
            plan=state.get("plan") or {},
            brief=state.get("brief") or {},
            budget=state.get("budget") or {},
            llm_mode="truncated_unrepaired",
            citations=citations,
            claims=seed_claims,
            metrics={
                **metrics,
                "llm_error": llm.last_error,
                "truncation_repaired": repaired,
            },
            terminal_followups=terminal_followups,
        )
        return _prefer_llm_or_compose(
            llm_markdown=markdown,
            compose_report_obj=failed,
            reason="truncated_unrepaired",
            metrics={
                **metrics,
                "llm_error": llm.last_error,
                "truncation_repaired": repaired,
            },
        )
    if not markdown.strip() or not memo_is_user_clean(markdown):
        failed = compose_terminal_synthesis(
            query=state.get("query") or "",
            evidence=evidence,
            critic=critic,
            plan=state.get("plan") or {},
            brief=state.get("brief") or {},
            budget=state.get("budget") or {},
            llm_mode="gemini_failed_fallback",
            citations=citations,
            claims=seed_claims,
            metrics={**metrics, "llm_error": llm.last_error},
            terminal_followups=terminal_followups,
        )
        cleaned = markdown
        if markdown.strip() and not memo_is_user_clean(markdown):
            drop_prefixes = tuple(
                m.lower()
                for m in (
                    "Research quality",
                    "Claim ledger",
                    "Critic status",
                    "Tool calls",
                    "Working set",
                    "Method and assumptions",
                    "Generator:",
                    "Graph:",
                )
            )
            cleaned_lines = []
            for line in markdown.splitlines():
                stripped = line.strip().lstrip("#*|>-").strip().lower()
                if stripped.startswith(drop_prefixes):
                    continue
                cleaned_lines.append(line)
            cleaned = "\n".join(cleaned_lines).replace("[?]", "")
        if _llm_memo_worth_keeping(cleaned):
            return _prefer_llm_or_compose(
                llm_markdown=cleaned,
                compose_report_obj=failed,
                reason="gemini_failed_fallback",
                metrics={**metrics, "llm_error": llm.last_error},
            )
        failed.metrics["synthesis_status"] = "gemini_failed_fallback"
        failed.metrics["llm_error"] = llm.last_error
        return failed

    from app.domain.report_audit import audit_memo

    audit_notes = audit_memo(markdown, query=user_goal(state.get("query") or ""))
    rewritten = False
    if audit_notes or int((critic.get("depth_score") or {}).get("score") or 0) < 65:
        markdown, rewritten = rewrite_for_quality(
            markdown,
            query=user_goal(state.get("query") or ""),
            audit_notes=audit_notes,
            criteria=race,
            critic=critic,
            max_tokens=report_max_tokens(depth),
        )
        markdown = polish_citations(markdown, citations=citations, evidence=evidence)
    critic_notes = audit_memo(markdown, query=user_goal(state.get("query") or "")) if rewritten else audit_notes
    parsed = parse_report_markdown(markdown)
    sidecar = _extract_report_sidecar(markdown, citations) or {}
    claims = seed_claims or base.claims
    if isinstance(sidecar.get("claims"), list) and sidecar["claims"]:
        try:
            claims = [Claim.model_validate(c) for c in sidecar["claims"]]
        except Exception:
            claims = seed_claims or base.claims
    limitations = sidecar.get("limitations") or parsed.get("limitations") or base.limitations
    if not isinstance(limitations, list):
        limitations = base.limitations
    limitations = [str(x) for x in limitations if str(x).strip()] + critic_notes
    open_questions = sidecar.get("open_questions") or base.open_questions
    if not isinstance(open_questions, list):
        open_questions = base.open_questions
    final_status = synthesis_status if terminal else "gemini_success"
    report = base.model_copy(
        update={
            "title": sidecar.get("title") or parsed.get("title") or base.title,
            "executive_summary": sidecar.get("executive_summary")
            or parsed.get("executive_summary")
            or base.executive_summary,
            "at_a_glance": sidecar.get("at_a_glance") or parsed.get("at_a_glance") or base.at_a_glance,
            "body_markdown": _ensure_budget_gap_notice(markdown, synthesis_status)
            if terminal
            else markdown,
            "decision_rule": sidecar.get("decision_rule") or parsed.get("decision_rule") or base.decision_rule,
            "limitations": limitations,
            "open_questions": [str(x) for x in open_questions if str(x).strip()],
            "claims": claims,
            "metrics": {
                **base.metrics,
                "synthesis_status": final_status,
                "writer": "markdown",
                "write_mode": write_mode,
                "truncation_repaired": repaired,
                "expansion_pass": expanded,
                "expansion_passes": expand_passes,
                "quality_rewrite": rewritten,
                "word_count": word_count(markdown),
                "compressed_notes": notes_prep != "raw_dossier",
                "notes_prep": notes_prep,
                **({"llm_error": llm.last_error} if llm.last_error else {}),
            },
        }
    )
    return report


def _compress_notes(state: ResearchState, dossier: list[dict], citations: list[dict]) -> tuple[str, str]:
    """Return (notes, prep_mode). prep_mode: raw_dossier | cleaned | cleaned_fallback."""
    depth = (state.get("brief") or {}).get("depth") or "standard"
    raw = format_research_notes(dossier, citations, depth=depth)
    # Deep: keep raw dossier notes — LLM "compress" historically dropped mechanisms/metrics.
    if should_skip_llm_compress(depth) or not llm.available:
        return raw, "raw_dossier"
    try:
        with use_role_model(llm, "report"):
            cleaned = llm.generate(
                compress_prompt(raw, user_goal(state.get("query") or ""), depth=depth),
                system=compress_system(),
                max_tokens=compress_max_tokens(depth),
            )
    except CreditsExhaustedError:
        raise
    except Exception:
        return raw, "cleaned_fallback"
    text = (cleaned or "").strip()
    # If the clean pass collapsed notes too aggressively, fall back to raw.
    if not text or len(text) < max(400, int(len(raw) * 0.35)):
        return raw, "cleaned_fallback"
    return text[: notes_max_chars(depth)], "cleaned"


def _generate_report_markdown(prompt: str, max_tokens: int) -> str:
    with use_role_model(llm, "writer"):
        return (
            llm.generate(
                prompt=prompt,
                system=writer_system(),
                max_tokens=max_tokens,
            )
            or ""
        ).strip()


def _extract_report_sidecar(markdown: str, citations: list[dict]) -> dict | None:
    try:
        with use_role_model(llm, "integrity"):
            payload = llm.generate_json(
                prompt=claims_prompt(markdown, citations),
                system="Extract structured fields from a Kiln memo. JSON only. Do not rewrite the memo.",
                max_tokens=2048,
            )
    except CreditsExhaustedError:
        return None
    return payload if isinstance(payload, dict) else None


def _comparison_subjects(query: str, evidence: list[dict]) -> list[str]:
    from app.domain.coverage import entities_with_evidence

    return entities_with_evidence(query, evidence, limit=4)


def _format_dossier_for_prompt(dossier: list[dict], citations: list[dict]) -> str:
    return format_research_notes(dossier, citations, depth="standard")


def _sanitize_decision_rule(query: str, rule: str, citations: list[dict], critic: dict) -> str:
    """Reject a rule that drifted off the asked subject, whatever that subject is."""
    from app.domain.citations import Citation
    from app.domain.research_intent import decision_rule_for

    ledger = []
    for c in citations:
        try:
            ledger.append(Citation.model_validate(c))
        except Exception:
            continue
    text = (rule or "").strip()
    if not text:
        return decision_rule_for(query, ledger, critic)
    anchors = distinctive_terms(user_goal(query), limit=8)
    if anchors and not any(term in text.lower() for term in anchors):
        return decision_rule_for(query, ledger, critic)
    return text


def _template_report(state: ResearchState, evidence: list[dict], critic: dict) -> Report:
    """Kept for tests that import the heuristic path."""
    return compose_report(
        query=state.get("query") or "",
        evidence=evidence,
        critic=critic,
        plan=state.get("plan") or {},
        brief=state.get("brief") or {},
        budget=state.get("budget") or {},
        llm_mode=state.get("llm_mode") or llm.mode,
    )


def _apply_integrity(
    report: Report,
    citations: list[dict],
    critic: dict,
    evidence: list[dict] | None = None,
    query: str = "",
) -> dict:
    from app.domain.report_integrity import enforce_report_integrity

    out = enforce_report_integrity(
        body_markdown=report.body_markdown or "",
        executive_summary=report.executive_summary or "",
        decision_rule=report.decision_rule or "",
        at_a_glance=getattr(report, "at_a_glance", "") or "",
        citations=citations,
        critic=critic,
        limitations=list(report.limitations or []),
        evidence=evidence,
        query=query,
    )
    return {
        **out,
        "metrics_patch": {
            "confidence_breakdown": out["confidence_breakdown"],
            "integrity_flags": out["integrity_flags"],
            "integrity_issues": out["integrity_issues"],
        },
    }


def _attach_evidence_graph(
    state: ResearchState,
    report: Report,
    evidence: list[dict],
    citations: list[dict],
    *,
    refetch: bool,
) -> None:
    from app.domain.verify_citations import verify_against_sources
    from app.persistence.evidence_graph import compact_graph, persist_evidence_graph
    from app.tools.fetch import fetch_url

    graph = verify_against_sources(
        report.claims,
        evidence,
        citations,
        refetch=refetch,
        fetch_fn=fetch_url if refetch else None,
    )
    report.metrics["evidence_graph"] = compact_graph(graph)
    persist_evidence_graph(str(state.get("thread_id") or ""), graph)


def _regenerate_for_quality(state: ResearchState) -> dict:
    """
    Quality-triggered regeneration: rewrite the memo from existing dimension-filtered notes
    with quality issues as additional instructions. Does NOT trigger new search.
    
    IMPORTANT: This function does NOT increment quality_regeneration_count.
    The counter is incremented by memo_gate BEFORE triggering this regeneration.
    This ensures a single shared counter across all regeneration paths (max 2 total).
    
    Flow:
    1. memo_gate detects quality issue
    2. memo_gate increments quality_regeneration_count
    3. memo_gate sets status = "revising_quality"
    4. report_node sees status → calls this function
    5. This function generates new report using existing evidence + quality feedback
    """
    from app.observability.logging import event
    
    event("report_quality_regenerate", issues="; ".join(state.get("quality_gate_issues", [])[:3]))
    
    # Get existing report and evidence
    prior_report = state.get("report") or {}
    retrieved = state.get("retrieved") or state.get("evidence") or []
    critic = state.get("critic") or {}
    budget = budget_from(state)
    
    # Build quality feedback prompt
    issues = state.get("quality_gate_issues") or []
    quality_instructions = "\n".join(f"- {issue}" for issue in issues[:5])
    quality_note = (
        f"\n\nQUALITY REQUIREMENTS - The previous draft had these issues:\n"
        f"{quality_instructions}\n"
        f"Fix these issues in the new draft. Use DIFFERENT passages for different dimensions. "
        f"Ensure every number has a clear metric and experimental condition.\n"
    )
    
    # Rebuild dossier and citations (they're already dimension-filtered from first pass)
    citations = [c.model_dump(mode="json") for c in build_ledger(retrieved)] or list(state.get("citations") or [])
    metrics = _metrics(state, budget)
    metrics["regeneration_trigger"] = "quality_gate"
    metrics["quality_issues"] = issues
    
    # Get seed claims from prior report
    seed_claims = None
    if prior_report.get("claims"):
        try:
            from app.domain.schema import Claim
            seed_claims = [Claim.model_validate(c) for c in prior_report["claims"]]
        except Exception:
            pass
    
    # Generate new report with quality feedback
    dossier = build_evidence_dossier(state.get("query") or "", retrieved, critic.get("coverage") or {})
    depth = (state.get("brief") or {}).get("depth") or "standard"
    
    # Calculate adaptive word target based on actual evidence
    coverage = critic.get("coverage") or {}
    adaptive_target = calculate_adaptive_target(dossier, coverage)
    min_words = adaptive_target["total_words"]
    adaptive_guidance = format_writer_guidance(adaptive_target)
    
    # P5: Graceful degradation - downgrade from 'deep' to 'standard' if evidence is sparse
    should_degrade, suggested_depth = should_use_graceful_degradation(adaptive_target)
    if should_degrade and depth == "deep":
        event("graceful_degradation_triggered_regen",
            original_depth=depth,
            new_depth=suggested_depth,
            reason=f"Evidence too sparse for deep synthesis (target: {min_words} words, "
                   f"coverage: {adaptive_target['coverage_tier']}, "
                   f"evidence_count: {adaptive_target['evidence_count']})"
        )
        depth = suggested_depth
    
    event("adaptive_depth_calculated_regen",
        target_words=min_words,
        coverage_tier=adaptive_target["coverage_tier"],
        evidence_count=adaptive_target["evidence_count"],
        dimension_count=adaptive_target["dimension_count"],
        rationale=adaptive_target["rationale"],
        final_depth=depth,
        degraded=should_degrade
    )
    
    dossier = filter_dossier_for_writer(
        prioritize_dossier_for_writer(dossier),
        depth=depth,
    )
    notes, notes_prep = _compress_notes(state, dossier, citations)
    metrics = {**metrics, "notes_prep": notes_prep}
    
    dimension_list = "\n".join(
        f"- {d.get('label')} (status: {d.get('status')})" for d in dossier if d.get("label")
    ) or "- Answer the question directly"
    
    subjects = _comparison_subjects(state.get("query") or "", retrieved)
    comparison_rule = (
        "Include a '## Comparison' table with one column per subject: "
        + ", ".join(subjects)
        + ". Every cell must come from a source that names that subject; otherwise write "
        "'Not established in collected sources'. Never repeat the same passage across columns.\n"
        if len(subjects) >= 2
        else "Omit the Comparison section — the question does not compare named subjects.\n"
    )
    
    method_block = method_notes_for_writer(
        user_goal(state.get("query") or ""),
        state.get("brief") or {},
        retrieved,
        citations,
    )
    
    # Inject quality feedback into writer prompt
    prompt = writer_prompt(
        query=user_goal(state.get("query") or ""),
        brief=state.get("brief") or {},
        notes=notes,
        citations=citations,
        min_words=min_words,
        comparison_rule=comparison_rule,
        prior_note="",
        dimension_list=dimension_list,
        method_block=method_block,
        adaptive_guidance=adaptive_guidance,
    ) + quality_note
    
    from app.report.race_write import (
        criteria_block,
        generate_sectionwise_memo,
        polish_citations,
        race_criteria,
    )
    
    race = race_criteria(user_goal(state.get("query") or ""), state.get("brief") or {}, dossier)
    prompt_with_race = f"{prompt}\n\n{criteria_block(race)}"
    
    tok_total = report_max_tokens(depth)
    tok_front = int(tok_total * 0.55) if str(depth).lower() == "deep" else tok_total
    tok_back = max(8192, tok_total - tok_front)
    
    markdown, write_mode = generate_sectionwise_memo(
        prompt_with_race,
        query=user_goal(state.get("query") or ""),
        notes=notes,
        citations=citations,
        criteria=race,
        comparison_rule=comparison_rule,
        depth=depth,
        max_tokens_front=tok_front,
        max_tokens_back=tok_back,
    )
    
    markdown = polish_citations(markdown, citations=citations, evidence=retrieved)

    # Build new report
    from app.domain.schema import Report, CitationRef
    
    parsed = parse_report_markdown(markdown)
    sidecar = _extract_report_sidecar(markdown, citations) or {}
    
    claims = seed_claims or []
    if isinstance(sidecar.get("claims"), list) and sidecar["claims"]:
        try:
            from app.domain.schema import Claim
            claims = [Claim.model_validate(c) for c in sidecar["claims"]]
        except Exception:
            pass
    
    limitations = sidecar.get("limitations") or parsed.get("limitations") or []
    if not isinstance(limitations, list):
        limitations = []
    
    open_questions = sidecar.get("open_questions") or []
    if not isinstance(open_questions, list):
        open_questions = []
    
    report = Report(
        title=sidecar.get("title") or parsed.get("title") or user_goal(state.get("query") or ""),
        executive_summary=sidecar.get("executive_summary") or parsed.get("executive_summary") or "",
        at_a_glance=sidecar.get("at_a_glance") or parsed.get("at_a_glance") or [],
        body_markdown=markdown,
        decision_rule=sidecar.get("decision_rule") or parsed.get("decision_rule") or "",
        limitations=[str(x) for x in limitations if str(x).strip()],
        open_questions=[str(x) for x in open_questions if str(x).strip()],
        claims=claims,
        citations=[CitationRef.model_validate(c) for c in citations],
        metrics={
            **metrics,
            "synthesis_status": "quality_regenerated",
            "writer": "markdown",
            "write_mode": f"{write_mode}_quality_regen",
            "word_count": word_count(markdown),
        },
    )
    
    if llm.last_tokens:
        budget.used_tokens += llm.last_tokens
        report.metrics["tokens"] = budget.used_tokens
        report.metrics["usd_est"] = round(budget.used_tokens / 1_000_000 * 0.40, 4)
    
    # Apply integrity checks
    integrity = _apply_integrity(report, citations, critic, retrieved, query=state.get("query") or "")
    report.body_markdown = integrity["body_markdown"]
    report.decision_rule = integrity["decision_rule"]
    report.at_a_glance = integrity["at_a_glance"]
    report.limitations = integrity["limitations"]
    report.metrics = {**report.metrics, **integrity["metrics_patch"]}
    
    # Bind citations
    report.body_markdown = bind_markdown_to_ledger(report.body_markdown or "", citations)
    report.body_markdown = annotate_inline_citation_tiers(report.body_markdown or "", citations)
    report.decision_rule = annotate_inline_citation_tiers(report.decision_rule or "", citations)
    
    event("report_quality_regenerated", word_count=word_count(markdown))
    
    # Return to normal memo_gate flow (status=draft so it goes to memo_gate, not back to quality loop)
    return {
        "report": dump(report),
        "claims": [dump(c) for c in report.claims],
        "budget": dump(budget),
        "llm_mode": llm.mode,
        "status": "draft",  # Clear revising_quality status
        "quality_gate_issues": [],  # Clear issues after regeneration
        "traces": [
            {
                "node": "report",
                "action": "quality_regenerate",
                "issues_fixed": len(issues),
                "word_count": word_count(markdown),
                "used_tokens_delta": llm.last_tokens or 0,
            }
        ],
    }
