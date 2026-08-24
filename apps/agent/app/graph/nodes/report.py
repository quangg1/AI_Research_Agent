from __future__ import annotations

import asyncio

from app.domain.citations import bind_markdown_to_ledger, build_ledger
from app.domain.coverage import build_evidence_dossier
from app.domain.grounding import verify_claims
from app.domain.knowledge import mark_reused, save_answer
from app.domain.research_intent import user_goal
from app.domain.schema import Claim, CitationRef, Report
from app.domain.textutil import distinctive_terms
from app.graph.serde import dump
from app.graph.state import ResearchState, budget_from
from app.llm.client import CreditsExhaustedError, llm
from app.observability.logging import event
from app.report.compose import (
    GRAPH_VERSION,
    compose_report,
    compose_terminal_synthesis,
    memo_is_user_clean,
)
from app.domain.adversarial import method_notes_for_writer
from app.report.deep_write import (
    claims_prompt,
    compress_max_tokens,
    compress_prompt,
    compress_system,
    format_research_notes,
    notes_max_chars,
    parse_report_markdown,
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
    terminal_status = state.get("status") if state.get("status") in {"out_of_scope", "cancelled"} else "completed"
    retrieved = state.get("retrieved") or state.get("evidence") or []
    critic = state.get("critic") or {}
    budget = budget_from(state)
    citations = [c.model_dump(mode="json") for c in build_ledger(retrieved)] or list(state.get("citations") or [])
    metrics = _metrics(state, budget)
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
    report.citations = [CitationRef.model_validate(c) for c in citations]
    report.body_markdown = bind_markdown_to_ledger(report.body_markdown or "", citations)
    report.decision_rule = _sanitize_decision_rule(state.get("query") or "", report.decision_rule or "", citations, critic)
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
    for claim in report.claims:
        allowed = {c.get("url") for c in citations}
        if claim.url and claim.url not in allowed:
            claim.url = next((c.get("url") or "" for c in citations if c.get("evidence_id") in (claim.support_ids or [])), "")
    _attach_evidence_graph(state, report, retrieved, citations, refetch=True)
    grounded = bool(citations) and any(claim.grounded for claim in report.claims)
    stored = (
        save_answer(
            state.get("query") or "",
            dump(report),
            critic.get("coverage") or {},
            prior_id=state.get("prior_knowledge_id"),
        )
        if terminal_status == "completed" and grounded
        else None
    )
    if stored:
        report.metrics["knowledge_id"] = stored.get("id")
        report.metrics["knowledge_version"] = stored.get("version")
        report.metrics["knowledge_sources"] = len(stored.get("citations") or [])
        if state.get("reuse_mode") == "augment":
            report.metrics["reuse_mode"] = "augment"
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
                "knowledge_version": report.metrics.get("knowledge_version"),
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
    if critic.get("status") == "insufficient" and budget.remaining_iterations <= 0:
        return "terminal_fallback"
    return "gemini_pending"


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
    return {
        "sources": len(state.get("retrieved") or state.get("evidence") or []),
        "iterations": budget.iterations,
        "tool_calls": budget.used_tool_calls,
        "tokens": budget.used_tokens,
        "usd_est": round(budget.used_tokens / 1_000_000 * 0.40, 4),
        "version": GRAPH_VERSION,
        "query_type": (state.get("plan") or {}).get("query_type") or state.get("query_type"),
        "coverage_ratio": cov.get("ratio"),
        "must_answer_fraction": cov.get("must_answer_fraction") or (depth.get("must_answer") or {}).get("fraction"),
        "critical_fraction": cov.get("critical_fraction") or (depth.get("critical") or {}).get("fraction"),
        "depth_score": depth.get("score"),
        "depth_label": depth.get("label"),
        "has_implementation": cov.get("has_implementation"),
        "unique_sources": cov.get("unique_sources"),
        "critical_gaps": [g.get("id") for g in (cov.get("critical_gaps") or [])],
        "must_answer": cov.get("slots") or [],
        "quality": depth,
    }


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
        critic.get("status") == "insufficient" and budget.remaining_iterations <= 0
    )

    if not llm.available:
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
    min_words = word_target(depth)
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
    )
    markdown = _generate_report_markdown(prompt, max_tokens=report_max_tokens(depth))
    if word_count(markdown) < max(200, int(min_words * 0.28)):
        shorter = writer_prompt(
            query=user_goal(state.get("query") or ""),
            brief=state.get("brief") or {},
            notes=notes[:12_000],
            citations=citations,
            min_words=max(700, int(min_words * 0.6)),
            comparison_rule=comparison_rule,
            prior_note=prior_note,
            dimension_list=dimension_list,
            method_block=method_block,
        )
        retry = _generate_report_markdown(shorter, max_tokens=report_max_tokens("standard"))
        if word_count(retry) > word_count(markdown):
            markdown = retry

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
        failed.metrics["synthesis_status"] = "gemini_failed_fallback"
        failed.metrics["llm_error"] = llm.last_error
        return failed

    from app.domain.report_audit import append_research_critic

    markdown, critic_notes = append_research_critic(
        markdown, query=user_goal(state.get("query") or "")
    )
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
    report = base.model_copy(
        update={
            "title": sidecar.get("title") or parsed.get("title") or base.title,
            "executive_summary": sidecar.get("executive_summary")
            or parsed.get("executive_summary")
            or base.executive_summary,
            "body_markdown": markdown,
            "decision_rule": sidecar.get("decision_rule") or parsed.get("decision_rule") or base.decision_rule,
            "limitations": limitations,
            "open_questions": [str(x) for x in open_questions if str(x).strip()],
            "claims": claims,
            "metrics": {
                **base.metrics,
                "synthesis_status": "gemini_success",
                "writer": "markdown",
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
