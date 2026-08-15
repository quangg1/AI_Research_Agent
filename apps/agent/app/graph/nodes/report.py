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
from app.llm.client import llm
from app.observability.logging import event
from app.report.compose import (
    GRAPH_VERSION,
    compose_report,
    compose_terminal_synthesis,
    memo_is_user_clean,
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
    min_words = 900 if depth == "deep" else 500
    dossier_text = _format_dossier_for_prompt(dossier, citations)
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
    prompt = (
        f"User question:\n{user_goal(state.get('query') or '')}\n\n"
        f"Research brief:\n{state.get('brief')}\n\n"
        f"{prior_note}"
        f"Dimensions this answer must cover:\n{dimension_list}\n\n"
        f"Evidence dossier (grouped by dimension):\n{dossier_text}\n\n"
        f"Citation ledger (ONLY these [n] are legal):\n{citations}\n\n"
        "Write a complete, reader-facing research memo that ANSWERS the question.\n"
        f"Minimum length: ~{min_words} words of substantive prose in body_markdown.\n"
        "Rules:\n"
        "- One '###' subsection per dimension above, in that order, inside '## Analysis'.\n"
        "- Explain the substance: what the source establishes, why it follows, and what it implies. "
        "Do not paste a quote and move on, and do not restate a dimension label as if it were a finding.\n"
        "- Every factual sentence carries an inline [n] citation from the ledger. Invent nothing.\n"
        "- When evidence for a dimension is missing, say so plainly in one sentence instead of speculating.\n"
        "- Keep disagreements between sources visible; never average them into a false consensus.\n"
        f"- {comparison_rule}"
        "- Exclude all internal telemetry: critic status, claim ledger, tool-call counts, iteration stats, "
        "working-set tables, or research-quality metrics.\n"
        "Required body_markdown sections:\n"
        "## Executive summary\n## Scope\n## Analysis\n## Comparison (only if applicable)\n"
        "## Findings\n## Decision rule\n## Limitations\n## References\n\n"
        "JSON keys: title, executive_summary, body_markdown, decision_rule, limitations (list), "
        "open_questions (list), claims (id,text,quote,url,tier,support_ids,contradict_ids,confidence,caveats)."
    )

    payload = _generate_report_json(prompt, max_tokens=8192)
    if not isinstance(payload, dict):
        payload = _generate_report_json(_compact_report_prompt(state, dossier_text, citations, min_words), max_tokens=4096)

    if not isinstance(payload, dict):
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

    try:
        payload["citations"] = citations
        payload["evidence"] = evidence
        payload.setdefault("method_notes", base.method_notes)
        payload.setdefault("metrics", dict(base.metrics))
        body = payload.get("body_markdown") or base.body_markdown
        if not memo_is_user_clean(body):
            body = base.body_markdown
        payload["body_markdown"] = body
        payload.setdefault("executive_summary", payload.get("executive_summary") or base.executive_summary)
        payload.setdefault("title", payload.get("title") or base.title)
        payload.setdefault("decision_rule", payload.get("decision_rule") or base.decision_rule)
        payload.setdefault("limitations", payload.get("limitations") or base.limitations)
        report = Report.model_validate(payload)
        report.metrics = {**base.metrics, **(report.metrics or {}), "synthesis_status": "gemini_success"}
        if llm.last_error:
            report.metrics["llm_error"] = llm.last_error
        return report
    except Exception:
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
        return failed


def _generate_report_json(prompt: str, max_tokens: int) -> dict | None:
    payload = llm.generate_json(
        prompt=prompt,
        system=(
            "You are Kiln's research writer. Write a complete reader-facing memo grounded strictly in the "
            "supplied evidence dossier, for whatever subject the question is about. "
            "No internal agent telemetry in body_markdown."
        ),
        max_tokens=max_tokens,
    )
    return payload if isinstance(payload, dict) else None


def _compact_report_prompt(state: ResearchState, dossier_text: str, citations: list[dict], min_words: int) -> str:
    return (
        f"User question:\n{user_goal(state.get('query') or '')}\n\n"
        f"Evidence dossier:\n{dossier_text}\n\n"
        f"Citations:\n{citations[:10]}\n\n"
        f"Write a COMPACT but complete memo (~{min_words} words). "
        "Sections: Executive summary, Scope, Analysis, Findings, Decision rule, Limitations, References. "
        "Explain substance with inline [n] citations. No critic/ledger/tool stats. JSON only."
    )


def _comparison_subjects(query: str, evidence: list[dict]) -> list[str]:
    from app.domain.coverage import entities_with_evidence

    return entities_with_evidence(query, evidence, limit=4)


def _format_dossier_for_prompt(dossier: list[dict], citations: list[dict]) -> str:
    url_to_n = {c.get("url", "").rstrip("/").lower(): c.get("n") for c in citations if c.get("url")}
    lines: list[str] = []
    for dim in dossier:
        items = dim.get("items") or []
        if not items:
            continue
        lines.append(f"### {dim.get('id')}: {dim.get('label')} (status: {dim.get('status')})")
        for ev in items[:2]:
            url = (ev.get("url") or "").rstrip("/").lower()
            n = url_to_n.get(url, "?")
            quote = (ev.get("quote") or ev.get("snippet") or "")[:320]
            lines.append(f"- [{n}] {ev.get('title') or url}: {quote}")
    return "\n".join(lines) if lines else "(no dimension-grouped evidence)"


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
