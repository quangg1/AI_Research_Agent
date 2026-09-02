from __future__ import annotations

import re

from app.domain.adversarial import (
    claim_register_markdown,
    competing_hypotheses,
    extract_quantitative_rows,
    quantitative_table_markdown,
    research_subquestions,
    source_quality_rows,
)
from app.domain.citations import Citation, build_ledger, format_reference_list, pick_quote
from app.domain.coverage import (
    GENERIC_CLAIM_RE,
    build_evidence_dossier,
    entities_with_evidence,
)
from app.domain.research_intent import (
    decision_rule_for,
    field_unknowns_for,
    is_secondary_host,
    user_goal,
)
from app.domain.schema import Claim, CitationRef, Report
from app.domain.textutil import distinctive_terms, entity_pattern, first_sentence_about

GRAPH_VERSION = "kiln-deep-research-2.4"

TIER_LABEL = {
    "official_regulation": "Primary docs",
    "intergovernmental": "Eval lab",
    "standard_body": "Framework",
    "peer_reviewed": "Research paper",
    "specialist_research": "Specialist / repo",
    "vendor_or_consultancy": "Vendor",
    "news_analysis": "Secondary",
    "unknown": "Unverified",
}

_TELEMETRY_MARKERS = (
    "Research quality",
    "Claim ledger",
    "Critic status",
    "Tool calls",
    "Working set",
    "Method and assumptions",
    "Generator:",
    "Graph:",
)

_NOT_ESTABLISHED = "Not established in collected sources"


def compose_report(
    *,
    query: str,
    evidence: list[dict],
    critic: dict | None = None,
    plan: dict | None = None,
    brief: dict | None = None,
    budget: dict | None = None,
    llm_mode: str = "heuristic",
    citations: list[dict] | None = None,
    claims: list[Claim] | None = None,
    metrics: dict | None = None,
    synthesis_status: str = "heuristic",
    terminal_followups: list | None = None,
) -> Report:
    critic = critic or {}
    plan = plan or {}
    brief = brief or {}
    budget = budget or {}
    ledger = [Citation.model_validate(c) for c in (citations or [])] or build_ledger(evidence)
    claims = claims or _claims_from_ledger(ledger, evidence)
    coverage = critic.get("coverage") or {}
    dossier = build_evidence_dossier(query, evidence, coverage)
    title = _title(query, brief)
    body = compose_user_memo(
        query=query,
        title=title,
        ledger=ledger,
        evidence=evidence,
        critic=critic,
        plan=plan,
        brief=brief,
        claims=claims,
        dossier=dossier,
        synthesis_status=synthesis_status,
        terminal_followups=terminal_followups,
        llm_mode=llm_mode,
    )
    from app.domain.report_audit import append_research_critic

    body, critic_notes = append_research_critic(body, query=user_goal(query))
    summary = _user_exec_summary(query, ledger, brief, dossier)
    rule = decision_rule_for(query, ledger, critic)
    from app.domain.report_integrity import _derive_at_a_glance

    diag = compose_diagnostics_payload(
        query=query,
        ledger=ledger,
        evidence=evidence,
        critic=critic,
        plan=plan,
        brief=brief,
        budget=budget,
        llm_mode=llm_mode,
        claims=claims,
        dossier=dossier,
        synthesis_status=synthesis_status,
        terminal_followups=terminal_followups,
    )
    merged_metrics = {
        "sources": len(ledger),
        "iterations": budget.get("iterations"),
        "tool_calls": budget.get("used_tool_calls"),
        "tokens": budget.get("used_tokens"),
        "version": GRAPH_VERSION,
        **(metrics or {}),
        **diag,
    }
    return Report(
        title=title,
        executive_summary=summary,
        body_markdown=body,
        claims=claims,
        evidence=_evidence_models(evidence),
        citations=[CitationRef.model_validate(c.model_dump()) for c in ledger],
        open_questions=_user_open_questions(critic, terminal_followups),
        method_notes=diag.get("method_notes") or [],
        limitations=_limitations(ledger, critic, llm_mode, terminal_followups) + critic_notes,
        decision_rule=rule,
        at_a_glance=_derive_at_a_glance(summary, rule, critic),
        metrics=merged_metrics,
    )


def compose_user_memo(
    *,
    query: str,
    title: str,
    ledger: list[Citation],
    evidence: list[dict],
    critic: dict,
    plan: dict,
    brief: dict,
    claims: list[Claim],
    dossier: list[dict] | None = None,
    synthesis_status: str = "heuristic",
    terminal_followups: list | None = None,
    llm_mode: str = "heuristic",
) -> str:
    dossier = dossier if dossier is not None else build_evidence_dossier(
        query, evidence, critic.get("coverage") or {}
    )
    return _user_memo_markdown(
        query,
        title,
        ledger,
        evidence,
        critic,
        brief,
        claims,
        dossier,
        synthesis_status,
        terminal_followups,
        llm_mode,
    )


def compose_terminal_synthesis(
    *,
    query: str,
    evidence: list[dict],
    critic: dict | None = None,
    plan: dict | None = None,
    brief: dict | None = None,
    budget: dict | None = None,
    llm_mode: str = "heuristic",
    citations: list[dict] | None = None,
    claims: list[Claim] | None = None,
    metrics: dict | None = None,
    terminal_followups: list | None = None,
) -> Report:
    """Best-effort complete memo when critic gaps remain or LLM synthesis failed."""
    return compose_report(
        query=query,
        evidence=evidence,
        critic=critic,
        plan=plan,
        brief=brief,
        budget=budget,
        llm_mode=llm_mode,
        citations=citations,
        claims=claims,
        metrics=metrics,
        synthesis_status="terminal_fallback",
        terminal_followups=terminal_followups,
    )


def compose_diagnostics_payload(
    *,
    query: str,
    ledger: list[Citation],
    evidence: list[dict],
    critic: dict,
    plan: dict,
    brief: dict,
    budget: dict,
    llm_mode: str,
    claims: list[Claim],
    dossier: list[dict],
    synthesis_status: str,
    terminal_followups: list | None,
) -> dict:
    cov = critic.get("coverage") or {}
    unresolved = [g.get("label") or g.get("id") for g in (cov.get("critical_gaps") or [])]
    if terminal_followups:
        for f in terminal_followups:
            q = f.get("question") if isinstance(f, dict) else str(f)
            if q:
                unresolved.append(q)
    return {
        "synthesis_status": synthesis_status,
        "generator": llm_mode,
        "critic_status": critic.get("status"),
        "gate_reason": (critic.get("coverage_gate") or {}).get("gate_reason") or critic.get("gate_reason"),
        "coverage_gate": critic.get("coverage_gate") or {},
        "unresolved_gaps": unresolved[:8],
        "diagnostics_markdown": _diagnostics_markdown(
            query, ledger, evidence, critic, plan, brief, budget, llm_mode, claims, dossier, synthesis_status
        ),
        "method_notes": [
            f"Generator: {llm_mode}",
            f"Synthesis: {synthesis_status}",
            f"Graph: {GRAPH_VERSION}",
            f"Critic: {critic.get('status') or 'n/a'}",
            "Diagnostics are operator-facing; the user memo excludes critic/coverage tables.",
        ],
    }


def memo_is_user_clean(body: str) -> bool:
    """True when the memo has no internal telemetry sections.

    Matching is anchored to line starts so a quoted source that happens to use one
    of these phrases mid-sentence is not mistaken for a leaked diagnostics block.
    """
    markers = tuple(m.lower() for m in _TELEMETRY_MARKERS)
    for line in (body or "").splitlines():
        stripped = line.strip().lstrip("#*|>-").strip().lower()
        if stripped.startswith(markers):
            return False
    if re.search(r"\[\?\]", body or ""):
        return False
    return True


def _title(query: str, brief: dict) -> str:
    goal = (brief.get("goal") or user_goal(query) or query or "Research memo").strip().split("\n")[0]
    return goal.rstrip("?")


def _dimensions(dossier: list[dict], with_items_only: bool = True) -> list[dict]:
    rows = [d for d in dossier if d.get("id") != "caveats"]
    if with_items_only:
        rows = [d for d in rows if d.get("items")]
    return rows


def _user_exec_summary(query: str, ledger: list[Citation], brief: dict, dossier: list[dict]) -> str:
    goal = user_goal(query)
    if not ledger:
        return (
            "Not enough grounded sources were collected to write a decision-grade memo. "
            "Treat any recommendation below as a gap note, not an answer."
        )
    top = ledger[:4]
    cites = " ".join(f"[{c.n}]" for c in top)
    depth = brief.get("depth") or "standard"
    first = pick_quote({"quote": top[0].quote}) if top else ""
    answered = _dimensions(dossier)
    total = len(_dimensions(dossier, with_items_only=False)) or 1
    covered_labels = ", ".join((d.get("label") or "")[:60] for d in answered[:3])
    coverage_line = (
        f"Evidence reaches {len(answered)} of {total} dimensions the question requires"
        + (f", including {covered_labels}. " if covered_labels else ". ")
    )
    return (
        f"This memo answers: {goal} "
        f"{coverage_line}"
        f"The analysis draws on {len(ledger)} cited sources {cites} at **{depth}** research depth. "
        + (f"Primary evidence opens with: “{first[:180]}” [{top[0].n}]. " if first else "")
        + "Where sources disagree, the contradiction is kept visible rather than averaged into a consensus."
    )


def _user_open_questions(critic: dict, terminal_followups: list | None) -> list[str]:
    cov = critic.get("coverage") or {}
    gaps = [g.get("label") or g.get("id") for g in (cov.get("critical_gaps") or []) if g.get("label") or g.get("id")]
    for slot in cov.get("slots") or []:
        if slot.get("status") in {"open", "weak"} and slot.get("critical"):
            label = slot.get("label") or slot.get("id")
            if label and label not in gaps:
                gaps.append(label)
    if terminal_followups:
        for f in terminal_followups[:3]:
            q = f.get("question") if isinstance(f, dict) else str(f)
            if q and q not in gaps:
                gaps.append(q)
    return gaps[:6]


def _cite_for_evidence(ev: dict, ledger: list[Citation]) -> str:
    url = (ev.get("url") or "").rstrip("/").lower()
    eid = ev.get("id")
    for c in ledger:
        if c.evidence_id == eid or (url and (c.url or "").rstrip("/").lower() == url):
            return f"[{c.n}]"
    return ""


def _source_name(ev: dict) -> str:
    title = (ev.get("title") or "").strip()
    if title:
        return title[:80]
    url = ev.get("url") or ""
    match = re.search(r"https?://([^/]+)", url)
    return match.group(1) if match else "an uncited source"


def _evidence_text(ev: dict) -> str:
    parts = [
        (ev.get("quote") or "").strip(),
        (ev.get("snippet") or "").strip(),
        (ev.get("full_text") or "")[:2000].strip(),
    ]
    return " ".join(p for p in parts if p)


def _relevant_sentences(ev: dict, patterns: list[str], topic_terms: list[str], limit: int = 2, *, dimension_label: str = "") -> list[str]:
    """Sentences in a source that actually speak to this dimension.
    
    If dimension_label is provided, will first select best passage for the dimension,
    then extract relevant sentences from that passage.
    """
    # Use dimension-specific passage if available
    if ev.get("selected_passage"):
        text = ev["selected_passage"]
    elif dimension_label:
        # Select best passage for this dimension
        from app.retrieval.passage import best_passage_for_claim
        full_text = _evidence_text(ev)
        if full_text:
            best_passage = best_passage_for_claim(
                full_text,
                dimension_label,
                patterns=patterns,
                topic_terms=topic_terms,
            )
            text = best_passage if best_passage else full_text
        else:
            text = full_text
    else:
        text = _evidence_text(ev)
    
    text = re.sub(r"\s+", " ", text)
    if not text:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?;])\s+", text) if len(s.strip()) > 40]
    scored: list[tuple[int, str]] = []
    for sentence in sentences:
        if GENERIC_CLAIM_RE.search(sentence):
            continue
        aspect = sum(1 for p in patterns if re.search(p, sentence, re.I))
        topic = sum(1 for t in topic_terms if t in sentence.lower())
        if aspect == 0 and topic == 0:
            continue
        scored.append((aspect * 3 + topic, sentence))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[str] = []
    for _, sentence in scored:
        trimmed = sentence[:340].rstrip()
        if trimmed not in out:
            out.append(trimmed)
        if len(out) >= limit:
            break
    return out


def _analysis_sections(query: str, dossier: list[dict], ledger: list[Citation], slots: list[dict]) -> str:
    """One evidence-grounded section per must-answer dimension."""
    anchors = distinctive_terms(user_goal(query), limit=10)
    by_id = {s.get("id"): s for s in slots}
    
    # Dossier already has per-dimension evidence with selected passages from coverage.py
    blocks: list[str] = []
    for dim in _dimensions(dossier):
        slot = by_id.get(dim.get("id")) or {}
        patterns = [p for p in (slot.get("patterns") or []) if p]
        topic_terms = [t for t in (slot.get("topic_terms") or anchors) if t]
        dim_label = dim.get("label") or ""
        
        lines: list[str] = []
        used_cites: list[str] = []
        for ev in dim.get("items") or []:
            cite = _cite_for_evidence(ev, ledger)
            sentences = _relevant_sentences(ev, patterns, topic_terms, dimension_label=dim_label)
            if not sentences:
                continue
            used_cites.append(cite)
            lead = _source_name(ev)
            body = " ".join(sentences)
            lines.append(f"{lead} states: “{body}” {cite}".rstrip())
        if not lines:
            continue
        marker = {"covered": "", "weak": " *(single or indirect source)*", "open": " *(unverified)*"}.get(
            dim.get("status") or "", ""
        )
        synthesis = _dimension_synthesis(dim, lines, used_cites)
        blocks.append(f"### {dim.get('label')}{marker}\n\n" + "\n\n".join(lines) + synthesis)
    if not blocks:
        return ""
    return "## Detailed analysis\n\n" + "\n\n".join(blocks) + "\n"


def _dimension_synthesis(dim: dict, lines: list[str], cites: list[str]) -> str:
    """A short reading of what the collected passages jointly establish."""
    unique = [c for c in dict.fromkeys(cites) if c]
    if len(lines) >= 2 and len(unique) >= 2:
        return (
            f"\n\nTaken together, {' and '.join(unique[:3])} agree on this dimension, "
            "so it can be treated as established rather than inferred."
        )
    if dim.get("status") == "weak":
        return (
            "\n\nOnly one source carries this dimension, so it should be treated as a hypothesis "
            "until a second independent source confirms it."
        )
    return ""


def _comparison_table(
    query: str,
    evidence: list[dict],
    dossier: list[dict],
    ledger: list[Citation],
    slots: list[dict],
) -> str:
    """Per-subject comparison where every cell is scoped to that subject's own sources."""
    entities = entities_with_evidence(query, evidence, limit=4)
    if len(entities) < 2:
        return ""
    dims = _dimensions(dossier, with_items_only=False)[:6]
    if not dims:
        return ""
    by_id = {s.get("id"): s for s in slots}
    anchors = distinctive_terms(user_goal(query), limit=10)
    header = " | ".join(["Dimension", *entities])
    separator = " | ".join(["---"] * (len(entities) + 1))
    rows: list[str] = []
    filled = 0
    for dim in dims:
        slot = by_id.get(dim.get("id")) or {}
        cells = []
        for entity in entities:
            cell = _comparison_cell(entity, dim, slot, evidence, ledger, anchors)
            if cell != _NOT_ESTABLISHED:
                filled += 1
            cells.append(cell)
        rows.append("| " + " | ".join([(dim.get("label") or "")[:60], *cells]) + " |")
    if filled < 2:
        return ""
    table = "\n".join([f"| {header} |", f"| {separator} |", *rows])
    return f"""## Comparison

{table}

Each cell is drawn only from a source that names that subject on that dimension. A blank is a genuine gap in
the collected evidence, not a claim of equivalence.
"""


def _comparison_cell(
    entity: str,
    dim: dict,
    slot: dict,
    evidence: list[dict],
    ledger: list[Citation],
    anchors: list[str],
) -> str:
    pattern = entity_pattern(entity)
    patterns = [p for p in (slot.get("patterns") or []) if p]
    topic_terms = [t for t in (slot.get("topic_terms") or anchors) if t]
    best: tuple[int, str, str] | None = None
    for ev in evidence:
        text = _evidence_text(ev)
        haystack = f"{ev.get('title', '')} {text}"
        if not re.search(pattern, haystack, re.I):
            continue
        sentence = ""
        for candidate in _relevant_sentences(ev, patterns, topic_terms, limit=3):
            if re.search(pattern, candidate, re.I):
                sentence = candidate
                break
        if not sentence:
            sentence = first_sentence_about(text, entity, limit=200)
        if not sentence or GENERIC_CLAIM_RE.search(sentence):
            continue
        score = sum(1 for p in patterns if re.search(p, sentence, re.I)) * 2 + sum(
            1 for t in topic_terms if t in sentence.lower()
        )
        if score <= 0:
            continue
        cite = _cite_for_evidence(ev, ledger)
        if best is None or score > best[0]:
            best = (score, sentence, cite)
    if not best:
        return _NOT_ESTABLISHED
    _, sentence, cite = best
    text = sentence[:180].rstrip().rstrip(".")
    return f"{text} {cite}".strip()


def _findings_narrative(
    query: str,
    ledger: list[Citation],
    dossier: list[dict],
    claims: list[Claim],
    critic: dict,
) -> str:
    cov = critic.get("coverage") or {}
    blocks: list[str] = []
    supported = [d for d in _dimensions(dossier) if d.get("status") == "covered"]
    partial = [d for d in _dimensions(dossier) if d.get("status") == "weak"]

    for dim in supported[:4]:
        cites = " ".join(
            dict.fromkeys(c for c in (_cite_for_evidence(ev, ledger) for ev in dim.get("items") or []) if c)
        )
        # Don't emit empty filler - only add if we have real synthesis
        label = dim.get('label') or ""
        if label and cites:
            blocks.append(f"**{label}** {cites}".strip())
    if partial:
        labels = "; ".join((d.get("label") or "")[:70] for d in partial[:3])
        blocks.append(
            f"**Thin evidence.** These dimensions rest on a single or indirect source and should not drive "
            f"a decision on their own: {labels}."
        )
    for note in (cov.get("contradictions") or [])[:3]:
        blocks.append(f"**Disagreement.** {note}")
    missing = [
        s.get("label")
        for s in (cov.get("slots") or [])
        if s.get("status") == "open" and (s.get("label") or s.get("id"))
    ]
    if missing:
        blocks.append(
            "**Not answered by this run.** " + "; ".join(str(m)[:70] for m in missing[:4]) + "."
        )
    if not blocks:
        for cl in claims[:5]:
            cite = next((f"[{r.n}]" for r in ledger if cl.url and r.url == cl.url), "")
            blocks.append(f"**{cl.text}** {cite}".strip())
    return "\n\n".join(blocks) or "_No findings could be grounded in the collected sources._"


def _user_memo_markdown(
    query: str,
    title: str,
    ledger: list[Citation],
    evidence: list[dict],
    critic: dict,
    brief: dict,
    claims: list[Claim],
    dossier: list[dict],
    synthesis_status: str,
    terminal_followups: list | None,
    llm_mode: str,
) -> str:
    cov = critic.get("coverage") or {}
    slots = cov.get("slots") or brief.get("must_answer") or []
    analysis = _analysis_sections(query, dossier, ledger, slots)
    comparison = _comparison_table(query, evidence, dossier, ledger, slots)
    findings = _findings_narrative(query, ledger, dossier, claims, critic)
    gap_note = ""
    if synthesis_status == "terminal_fallback" or critic.get("status") == "insufficient":
        if _user_open_questions(critic, terminal_followups):
            gap_note = (
                "\n> **Note:** Some dimensions could not be verified within the research budget. "
                "The analysis below reflects the strongest evidence collected; open items are listed "
                "under Limitations.\n"
            )
    limitations = "\n".join(f"- {x}" for x in _limitations(ledger, critic, llm_mode, terminal_followups))
    hyps = brief.get("hypotheses") or competing_hypotheses(query)
    cite_dicts = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in ledger]
    numbers = extract_quantitative_rows(evidence, cite_dicts)
    quality = source_quality_rows(evidence, cite_dicts)
    quality_md = "\n".join(
        f"- [{row['n']}] {row['band']} / {row.get('publication_status') or 'unknown'} — {row['title']}"
        + (f" ({row['year']})" if row.get("year") else "")
        for row in quality[:16]
    ) or "- No sources to band."
    # Numbered key findings for ODR-style memos.
    key_findings = "\n".join(
        f"{i}. {line.lstrip('- ').strip()}"
        for i, line in enumerate(
            (findings.replace("**", "").split("\n\n") if findings else []),
            start=1,
        )
        if line.strip() and not line.strip().startswith("_")
    ) or findings
    parts = [
        f"# {title}",
        "## Executive summary",
        _user_exec_summary(query, ledger, brief, dossier),
        gap_note,
        "## Key findings",
        key_findings,
        analysis or "## Detailed analysis\n\n_No dimension-level evidence available._",
        "## Quantitative findings",
        quantitative_table_markdown(numbers)
        if numbers
        else "_No measured values extracted from collected excerpts._\n\n### Metric gaps\n\n"
        "- Asked quantitative metrics were not present as numbers in the collected excerpts.",
        "## Contradictions & debates",
        "\n".join(f"- {h}" for h in hyps[:2]),
        comparison,
        "## Decision rule",
        decision_rule_for(query, ledger, critic),
        "## Uncertainties & gaps",
        "\n".join(f"- {u}" for u in field_unknowns_for(query, critic)),
        "## Limitations",
        limitations,
        "## Source quality",
        quality_md,
        "## References",
        format_reference_list(ledger),
    ]
    memo = "\n\n".join(p.strip() for p in parts if (p or "").strip()) + "\n"
    
    # Soften overclaim language
    from app.domain.overclaim import soften_overclaims
    from app.observability.logging import logger
    
    softened_memo, changes = soften_overclaims(memo, aggressive=False)
    
    if changes:
        logger.info(
            f"overclaim_softened: {len(changes)} absolute terms softened",
            extra={"changes": [{"from": c["original"], "to": c["replacement"]} for c in changes[:3]]}
        )
    
    return softened_memo


def _diagnostics_markdown(
    query: str,
    ledger: list[Citation],
    evidence: list[dict],
    critic: dict,
    plan: dict,
    brief: dict,
    budget: dict,
    llm_mode: str,
    claims: list[Claim],
    dossier: list[dict],
    synthesis_status: str,
) -> str:
    """Operator-facing telemetry — not shown in the default user memo."""
    goal = user_goal(query)
    agents = ", ".join(plan.get("agents_to_run") or []) or "docs"
    qtype = plan.get("query_type") or brief.get("query_type") or "open_research"
    rows = "\n".join(
        f"| {c.n} | {c.host or 'corpus'} | {TIER_LABEL.get(c.tier, c.tier or '—')} | {(c.title or '')[:48]} |"
        for c in ledger
    )
    claim_rows = "\n".join(
        f"| {getattr(cl, 'id', None) or i+1} | {(cl.text or '')[:110]} | "
        f"{int(float(cl.confidence or 0)*100)}% | {getattr(cl, 'evidence_type', None) or '—'} | "
        f"{getattr(cl, 'directness', None) or ('yes' if cl.grounded else 'weak')} |"
        for i, cl in enumerate(claims[:12])
    ) or "| — | No must-answer claims | — | — | — |"
    coverage_md = _coverage_table(critic, brief)
    dossier_rows = []
    for dim in dossier:
        items = dim.get("items") or []
        srcs = ", ".join((e.get("title") or e.get("url") or "?")[:40] for e in items[:2]) or "—"
        dossier_rows.append(
            f"| {dim.get('id')} | {(dim.get('label') or '')[:50]} | {dim.get('status')} | {len(items)} | {srcs} |"
        )
    dossier_table = "\n".join(dossier_rows) or "| — | — | — | 0 | — |"
    contra_md = "\n".join(f"- {r}" for r in (critic.get("reasons") or [])) or "- No critic contradiction recorded."
    return f"""# Research diagnostics (operator)

Query: {goal}
Synthesis: **{synthesis_status}** · Generator: **{llm_mode}** · Graph: `{GRAPH_VERSION}`

{coverage_md}
## Run budget

| Variable | Value |
| --- | --- |
| Query class | {qtype} |
| Agents | {agents} |
| Depth | {brief.get("depth") or "deep"} |
| Iterations | {budget.get("iterations") or 1} / {budget.get("max_iterations") or 6} |
| Retrieval calls | {budget.get("used_retrieval_calls") or 0} / {budget.get("max_retrieval_calls") or 28} |
| Enrich calls | {budget.get("used_enrich_calls") or 0} / {budget.get("max_enrich_calls") or 24} |
| Tool calls (total) | {budget.get("used_tool_calls") or 0} / {budget.get("max_tool_calls") or 52} |
| Tokens (est.) | {budget.get("used_tokens") or 0} |

## Evidence dossier (by must-answer dimension)

| Dimension | Label | Status | Sources | Top titles |
| --- | --- | --- | --- | --- |
{dossier_table}

## Working set

| # | Host | Tier | Title |
| --- | --- | --- | --- |
{rows}

## Claim ledger

| # | Claim | Conf. | Evidence type | Directness |
| --- | --- | --- | --- | --- |
{claim_rows}

## Critic notes

{contra_md}
"""


def _coverage_table(critic: dict, brief: dict) -> str:
    cov = critic.get("coverage") or {}
    slots = cov.get("slots") or brief.get("must_answer") or []
    depth = critic.get("depth_score") or cov.get("depth_score") or {}
    if not slots and not depth:
        return ""
    must = depth.get("must_answer") or {}
    crit = depth.get("critical") or {}
    must_frac = (
        must.get("fraction")
        or cov.get("must_answer_fraction")
        or f"{cov.get('covered', 0)}/{cov.get('total', len(slots))}"
    )
    crit_frac = crit.get("fraction") or cov.get("critical_fraction") or "?"
    must_pct = must.get("pct")
    if must_pct is None and cov.get("ratio") is not None:
        must_pct = int(round(100 * float(cov["ratio"])))
    crit_pct = crit.get("pct")
    if crit_pct is None and cov.get("critical_ratio") is not None:
        crit_pct = int(round(100 * float(cov["critical_ratio"])))

    def bar(pct) -> str:
        filled = max(0, min(10, int(round((pct or 0) / 10))))
        return "█" * filled + "░" * (10 - filled)

    breakdown = depth.get("breakdown") or {}
    extra_rows = "\n".join(
        f"| {key.replace('_', ' ').capitalize()} | **{value}%** | `{bar(value)}` |"
        for key, value in breakdown.items()
        if key not in {"must_answer_coverage", "critical_coverage"}
    )
    strong = cov.get("strong_slots") or [s for s in slots if s.get("status") == "covered"]
    weak = cov.get("weak_slots") or [s for s in slots if s.get("status") == "weak"]
    strong_md = "\n".join(f"- ✓ {s.get('label') or s.get('id')}" for s in strong) or "- —"
    weak_md = "\n".join(f"- ⚠ {s.get('label') or s.get('id')}" for s in weak) or "- —"
    rows = "\n".join(
        f"| {({'covered': '✓', 'weak': '⚠', 'open': '✗'}).get((s.get('status') or 'open').lower(), '?')} "
        f"| {(s.get('status') or 'open').lower()} "
        f"| {'critical' if s.get('critical') or s.get('priority') == 'critical' else 'standard'} "
        f"| {s.get('label') or s.get('id')} |"
        for s in slots
    ) or "| — | — | — | No dimensions |"

    return f"""## Research quality

| Metric | Value | |
| --- | --- | --- |
| Must-answer coverage | {must_frac} = **{must_pct}%** | `{bar(must_pct)}` |
| Critical coverage | {crit_frac} = **{crit_pct}%** | `{bar(crit_pct)}` |
{extra_rows}
| Unique sources (deduped) | **{cov.get('unique_sources') or depth.get('unique_sources') or '—'}** | |
| Overall research confidence | **{depth.get('score')}/100** ({depth.get('label') or ''}) | |
| Critic | **{(critic.get('status') or 'n/a').upper()}** | Issue threads are not implementation evidence |

| | Status | Priority | Must-answer dimension |
| --- | --- | --- | --- |
{rows}

### Strong evidence
{strong_md}

### Weak / open evidence
{weak_md}

"""


def _limitations(
    ledger: list[Citation],
    critic: dict,
    llm_mode: str,
    terminal_followups: list | None = None,
) -> list[str]:
    hosts = {c.host for c in ledger if c.host}
    secondary = [c.host for c in ledger if is_secondary_host(c.url or c.host or "")]
    cov = critic.get("coverage") or {}
    out = [
        f"This analysis uses {len(ledger)} sources across {len(hosts) or 0} hosts — not a complete review of the literature.",
        "Snippet-level retrieval can miss PDF annexes, source files, and paywalled material.",
        "Ranking prefers primary papers, official docs, and source repositories; secondary write-ups are pointers, not proof.",
    ]
    open_slots = [
        s.get("label") or s.get("id")
        for s in (cov.get("slots") or [])
        if s.get("status") in {"open", "weak"} and s.get("critical")
    ]
    if open_slots:
        out.append("These critical dimensions remain weak or unverified: " + "; ".join(map(str, open_slots[:5])) + ".")
    if terminal_followups:
        qs = [(f.get("question") if isinstance(f, dict) else str(f)) for f in terminal_followups[:3]]
        qs = [q for q in qs if q]
        if qs:
            out.append("Further research would target: " + "; ".join(qs) + ".")
    if secondary:
        out.append(f"Secondary hosts remain in the set ({', '.join(sorted(set(secondary))[:4])}) — treat as pointers only.")
    if critic.get("status") == "contradicted":
        out.append("Sources disagree on key details; do not read a single headline number as the answer.")
    else:
        out.append("Re-run retrieval before treating time-sensitive details as current.")
    return out


def _claims_from_ledger(ledger: list[Citation], evidence: list[dict]) -> list[Claim]:
    """Fallback when extract/critic did not supply must-answer claims."""
    from app.domain.research_intent import claim_confidence

    by_id = {e.get("id"): e for e in evidence}
    claims: list[Claim] = []
    for c in ledger[:6]:
        ev = by_id.get(c.evidence_id) or {
            "url": c.url,
            "tier": c.tier,
            "quote": c.quote,
            "snippet": c.quote,
            "credibility": 0.5,
        }
        quote = c.quote or ""
        text = quote if quote and not GENERIC_CLAIM_RE.search(quote) else (c.title or quote[:200])
        if GENERIC_CLAIM_RE.search(text or ""):
            text = c.title or "Source-backed finding"
            quote = ""
        claims.append(
            Claim(
                id=f"C{c.n}",
                text=(text or c.title)[:400],
                quote=quote[:400],
                url=c.url,
                tier=c.tier,
                support_ids=[c.evidence_id],
                confidence=claim_confidence(ev),
                caveats=(
                    ["Secondary source — corroborate with a primary source"]
                    if is_secondary_host(c.url)
                    else []
                ),
                grounded=True,
                evidence_type="primary_paper" if "arxiv" in (c.host or "") else "specialist",
                directness="direct",
            )
        )
    return claims


def _evidence_models(evidence: list[dict]):
    from app.domain.schema import Evidence

    out = []
    for e in evidence:
        data = {k: v for k, v in e.items() if k != "full_text"}
        try:
            out.append(Evidence.model_validate(data))
        except Exception:
            continue
    return out
