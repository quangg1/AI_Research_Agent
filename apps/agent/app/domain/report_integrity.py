"""Post-write integrity pass: catch memo self-contradictions before publish."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.domain.metric_grounding import split_sentences
from app.domain.report_audit import _section
from app.domain.research_intent import user_goal
from app.domain.schema import AgentName, SubQuery
# Sections where citation lists should remain intact (not be stripped/decluttered),
# and where the memo's OWN diagnostic prose lives -- Limitations bullets quote
# citation numbers and figures as descriptive text about a problem (e.g.
# "Citation marker(s) [6], [7] referenced a source..."), not as new claims to
# verify. Scanning it as ordinary prose let one pass's folded finding get
# re-discovered as new "evidence" by the next pass's audit, compounding
# across report-regeneration retries into garbled, ever-growing bullets.
PROTECTED_SECTIONS = ("Source quality", "References", "Limitations")

UNRESOLVED_CITE_RE = re.compile(r"\[\?\]")
_CITE_ONE = r"\d+(?:\s+[A-Za-z]+)?"
CITE_RE = re.compile(rf"\[({_CITE_ONE}(?:\s*,\s*{_CITE_ONE})*)\]")
LOAD_NUMBER_RE = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:%|percent|tokens?|tok/s|ms|GB|MB|OCPU|vCPU)?\b",
    re.I,
)
QUANT_ABSENT_RE = re.compile(
    r"measurements?\s+absent|no\s+measured|not\s+reported\s+in\s+source|"
    r"none\s+extracted|metric\s+gaps\s+only|absent\s+in\s+source",
    re.I,
)
THRESHOLD_IN_RULE_RE = re.compile(
    r"(?:≥|<=|>=|<|≤|above|below|exceeds?|at\s+least)\s*\d+(?:\.\d+)?\s*%?",
    re.I,
)
ILLUSTRATIVE_PREFIX = (
    "> **Illustrative scenario (not measured in collected sources):** "
    "Numbers below are for intuition only — they do not appear in the citation ledger.\n\n"
)


def audit_memo_integrity(
    text: str,
    *,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
    executive_summary: str = "",
    at_a_glance: str = "",
) -> list[str]:
    blob = text or ""
    notes: list[str] = []
    quant = _section(blob, "Quantitative findings")
    quant_absent = _quantitative_data_absent(quant)

    if UNRESOLVED_CITE_RE.search(blob):
        notes.append(
            "Unresolved citation placeholders [?] appear in the memo. "
            "Every [n] must map to the ledger before publish."
        )

    if quant_absent:
        for heading in ("Decision rule", "Worked example"):
            body = _section(blob, heading)
            if not body:
                continue
            uncited = _uncited_load_bearing_numbers(body)
            if uncited:
                notes.append(
                    f"Internal contradiction: Quantitative findings admits no measured data, "
                    f"but {heading} cites specific figures ({', '.join(sorted(uncited)[:5])}) "
                    f"without source backing. Label as illustrative or remove."
                )
        empirical = _section(blob, "Decision rule")
        if empirical and THRESHOLD_IN_RULE_RE.search(empirical):
            if "engineering heuristic" not in empirical.lower() and "illustrative" not in empirical.lower():
                notes.append(
                    "Decision rule lists numeric cutoffs (e.g. % thresholds) while the quantitative "
                    "table is empty — move cutoffs to Engineering heuristics or cite a measured threshold."
                )

    stacks = _citation_stacks(blob)
    if stacks:
        notes.append(
            f"Citation stacking: {len(stacks)} sentence(s) attach 3+ sources at once "
            f"(e.g. [{stacks[0]}]). Prefer 1–2 citations per claim with a supporting quote."
        )

    concentration = _load_bearing_source_concentration(blob, citations, executive_summary, at_a_glance)
    if concentration:
        notes.append(concentration)

    exec_clean = _normalize_prose(executive_summary)
    glance_clean = _normalize_prose(at_a_glance)
    if exec_clean and glance_clean:
        ratio = SequenceMatcher(None, exec_clean, glance_clean).ratio()
        if ratio >= 0.82:
            notes.append(
                "At a glance duplicates the Executive summary. "
                "Glance should be one decision line + top caveat; summary stays analytical."
            )
    elif exec_clean and not glance_clean:
        exec_body = _normalize_prose(_section(blob, "Executive summary"))
        if exec_body and SequenceMatcher(None, exec_clean, exec_body).ratio() >= 0.82:
            notes.append(
                "UI 'At a glance' will mirror Executive summary unless a distinct glance line is added."
            )

    tone = _tone_mismatch(blob)
    if tone:
        notes.append(tone)
    
    # NEW: Verify all numbers in body, not just Quantitative table
    # Catches misattributions in prose, Comparison, Worked example.
    # Exclude Limitations: it's this same audit's own output on a report-
    # regeneration retry, not new prose to fact-check (see PROTECTED_SECTIONS).
    body_number_issues = audit_body_numbers(
        _strip_protected_sections(blob), citations=citations, evidence=evidence
    )
    notes.extend(body_number_issues)

    backbone_note = _low_tier_quantitative_backbone_note(blob, citations)
    if backbone_note:
        notes.append(backbone_note)

    return notes


def _low_tier_quantitative_backbone_note(blob: str, citations: list[dict] | None) -> str:
    """Flag when the memo's load-bearing numbers trace mostly to Band C
    (news/vendor/unknown-tier) sources — a "Primary sources 100%" confidence
    line only reflects host-based role classification (arxiv/github/docs),
    not the tier of whichever source actually backs each cited figure, so a
    memo can score full "primary sources" while its Quantitative findings /
    Key findings numbers are backed almost entirely by an SEO blog or a
    LinkedIn post the Source quality section itself already tags "unknown".
    """
    if not citations:
        return ""
    from app.domain.adversarial import quality_band

    tier_by_n: dict[int, str] = {}
    for c in citations:
        n = c.get("n") if isinstance(c, dict) else None
        try:
            n_int = int(n)
        except (TypeError, ValueError):
            continue
        tier_by_n[n_int] = str(c.get("tier") or "")

    cited: set[int] = set()
    for heading in ("Quantitative findings", "Key findings"):
        section = _section(blob, heading)
        if not section:
            continue
        for m in CITE_RE.finditer(section):
            for part in m.group(1).split(","):
                digits = re.search(r"\d+", part.strip())
                if digits:
                    cited.add(int(digits.group(0)))

    known = [n for n in cited if n in tier_by_n]
    if len(known) < 2:
        return ""
    low_tier = [n for n in known if quality_band(tier_by_n[n]) == "C"]
    if len(low_tier) / len(known) <= 0.5:
        return ""
    return (
        f"Load-bearing numbers in Quantitative findings / Key findings rely mostly on "
        f"Band C (unverified/vendor/unknown-tier) sources ({', '.join(f'[{n}]' for n in sorted(low_tier))}) "
        f"despite a high overall confidence score — treat the specific figures as directional, "
        f"not authoritative, until corroborated by a primary paper or measured benchmark."
    )


def enforce_report_integrity(
    *,
    body_markdown: str,
    executive_summary: str,
    decision_rule: str,
    at_a_glance: str,
    citations: list[dict],
    critic: dict,
    limitations: list[str],
    evidence: list[dict] | None = None,
    query: str = "",
) -> dict:
    """Deterministic repairs + integrity metadata."""
    body = body_markdown or ""
    limitations = list(limitations or [])
    flags: list[str] = []

    if UNRESOLVED_CITE_RE.search(body):
        body = UNRESOLVED_CITE_RE.sub("", body)
        flags.append("stripped_unresolved_cites")
        limitations.append(
            "Some citation markers could not be resolved and were removed before publish."
        )

    # A real numeric marker (not the literal "[?]" placeholder above) can
    # still point at nothing: the writer cites a source it planned to use
    # (e.g. a mandatory primary that was never actually fetched into the
    # ledger) as "[6]"/"[7]", and bind_markdown_to_ledger only rebuilds
    # References from `citations` -- it never scrubs a bare prose marker
    # with no matching entry. Real run: [6]/[7] cited 4 times in the body,
    # absent from References entirely, with no way for the reader to look
    # them up.
    if citations:
        valid_ns = {n for c in citations if (n := _as_cite_n(c)) is not None}
        cited_ns_in_prose: set[int] = set()
        for group in CITE_RE.findall(_strip_protected_sections(body)):
            for piece in group.split(","):
                m = re.match(r"\s*(\d+)", piece)
                if m:
                    cited_ns_in_prose.add(int(m.group(1)))
        dangling = sorted(cited_ns_in_prose - valid_ns)
        if dangling:
            body = _strip_cite_markers_outside_protected(body, dangling)
            flags.append("stripped_dangling_citation_numbers")
            marker_list = ", ".join(f"[{n}]" for n in dangling)
            limitations.append(
                f"Citation marker(s) {marker_list} referenced a source that was never added "
                "to the reference ledger and were removed before publish."
            )

    quant = _section(body, "Quantitative findings")
    quant_absent = _quantitative_data_absent(quant)

    if quant_absent:
        body = _label_illustrative_section(body, "Worked example")
        decision_rule = _sanitize_decision_rule_numbers(decision_rule, quant_absent=True)
        body = _rewrite_decision_rule_section(body, decision_rule)
    elif evidence is not None:
        # The main table can be fully grounded while the Worked Example still
        # carries its own cited numbers that sanitize_quantitative_table never
        # touches (it only polices the Quantitative findings table). Check
        # those separately so a confident-looking [n specialist] on a made-up
        # token count can't slip through just because the real table is fine.
        worked = _section(body, "Worked example")
        if worked and _worked_example_mostly_ungrounded(worked, citations=citations or [], evidence=evidence):
            body = _insert_illustrative_prefix(body, "Worked example")
            flags.append("worked_example_ungrounded")
    
    # NEW: Label composite worked examples instead of triggering regeneration
    # Check if Worked example cites multiple sources without "Composite" label
    worked = _section(body, "Worked example")
    if worked:
        cite_nums = set()
        for m in CITE_RE.finditer(worked):
            parts = m.group(1).split(",")
            for p in parts:
                digits = re.search(r"\d+", p.strip())
                if digits:
                    cite_nums.add(int(digits.group(0)))
        
        if len(cite_nums) >= 2 and "composite" not in worked.lower():
            # Add composite label instead of regenerating
            composite_prefix = (
                "> **Composite** — steps drawn from multiple separate systems "
                "that were not evaluated together.\n\n"
            )
            body = _insert_prefix_to_section(body, "Worked example", composite_prefix)
            flags.append("worked_example_composite_labeled")
    
    # NEW: Drop empty sections (< 3 lines, no citations)
    body = _drop_empty_sections(body)
    if _drop_empty_sections(body) != body:
        flags.append("empty_sections_dropped")

    # Independent of quant_absent: _sanitize_decision_rule_numbers only drops
    # numbers with NO citation, trusting any [n] as proof — but a cited
    # number can still be wrong if it was actually said by a *different*
    # source than the one numbered here (real memo output: "76% ... [9
    # specialist]" where 76% came from an uncited vendor blog, not source 9).
    # Verify each cited number against the source it actually names.
    if evidence is not None and (decision_rule or "").strip():
        cleaned_rule = _strip_ungrounded_decision_numbers(
            decision_rule, citations=citations or [], evidence=evidence
        )
        if cleaned_rule != decision_rule:
            decision_rule = cleaned_rule
            body = _rewrite_decision_rule_section(body, decision_rule)
            flags.append("decision_rule_misattributed_number_dropped")

    if evidence is not None and query:
        cleaned_body, n_stripped = _strip_ungrounded_entity_citations(
            body, citations=citations or [], evidence=evidence, query=query
        )
        if n_stripped:
            body = cleaned_body
            flags.append("entity_citation_misattributed_stripped")
            limitations.append(
                "Some named framework/product descriptions could not be verified against "
                "retrieved sources and their citations were removed."
            )

    # Subject/scope gates: demote preference/abstention sections and strip
    # scale-mismatched quantitative attributions on memory/VRAM queries.
    # Soft-demotes claims/sections only — never blanks [n] inside References.
    if query:
        from app.domain.metric_grounding import demote_off_scope_memo_content

        demoted_body, demote_flags = demote_off_scope_memo_content(
            body,
            query=query,
            citations=citations or [],
            evidence=evidence,
        )
        if demote_flags:
            body = demoted_body
            flags.extend(demote_flags)
            limitations.append(
                "Some memo sections/claims were demoted because cited sources did not "
                "ground the asked subject (model scale or memory-vs-alignment scope)."
            )
    
    # Check citation relevance - prevent off-topic papers from being cited
    # (e.g. biology/neuroscience papers for AI/ML claims). Never mutate
    # ## References / ## Source quality: blanking [n] inside **[n]** leaves
    # **** and empty Other-sources bands (live run ff3c5688).
    if evidence is not None:
        from app.domain.citation_relevance import check_citation_relevance

        off_topic_ns: list[int] = []
        for ev in evidence:
            is_relevant, issues = check_citation_relevance(ev, query or "", strict=True)
            if is_relevant or not issues:
                continue
            ev_url = (ev.get("url") or "").strip().rstrip("/").lower()
            for cit in (citations or []):
                cit_url = (cit.get("url") or "").strip().rstrip("/").lower()
                if cit_url != ev_url:
                    continue
                n = cit.get("n")
                if n is None or n == "":
                    break
                try:
                    n_int = int(n)
                except (TypeError, ValueError):
                    break
                off_topic_ns.append(n_int)
                flags.append(f"off_topic_citation_stripped_{n_int}")
                limitations.append(
                    f"Source [{n_int}] removed: {issues[0] if issues else 'off-topic for query domain'}"
                )
                break
        if off_topic_ns:
            body = _strip_cite_markers_outside_protected(body, off_topic_ns)
            # Rebuild ledger lists so References never keep **** and Source
            # quality never keeps empty Other-sources stubs after a strip.
            try:
                from app.domain.citations import bind_markdown_to_ledger

                kept = [
                    c
                    for c in (citations or [])
                    if _as_cite_n(c) is not None and _as_cite_n(c) not in set(off_topic_ns)
                ]
                if kept:
                    body = bind_markdown_to_ledger(body, kept)
            except Exception:
                body = body.replace("****", "")

    # Always rebuild Source quality / References from the ledger after any
    # cite-marker surgery. This drops empty Other-sources stubs and repairs
    # **** left by older strippers, matching bind_markdown_to_ledger's contract.
    if citations:
        try:
            from app.domain.citations import bind_markdown_to_ledger

            body = bind_markdown_to_ledger(body, citations)
        except Exception:
            body = body.replace("****", "")
            body = _drop_empty_other_sources_band(body)

    audit_notes = audit_memo_integrity(
        body,
        citations=citations,
        evidence=evidence,
        executive_summary=executive_summary,
        at_a_glance=at_a_glance,
    )
    limitations.extend(audit_notes)
    # The entity gate's own finding lives in critic.reasons and stopped there:
    # a framework the question named explicitly can be missing from the memo
    # with nothing telling the reader (real run: the question named five agent
    # frameworks, the memo compared four, and the gap showed up only in
    # metrics_json).
    for reason in (critic or {}).get("reasons") or []:
        text = str(reason).strip()
        if _NAMED_SUBJECT_GAP_RE.match(text) and text not in limitations:
            limitations.append(text)

    if not (at_a_glance or "").strip():
        at_a_glance = _derive_at_a_glance(executive_summary, decision_rule, critic)
    elif _normalize_prose(at_a_glance) and _normalize_prose(executive_summary):
        if SequenceMatcher(None, _normalize_prose(at_a_glance), _normalize_prose(executive_summary)).ratio() >= 0.82:
            at_a_glance = _derive_at_a_glance(executive_summary, decision_rule, critic)
            flags.append("regenerated_at_a_glance")

    depth = (critic.get("depth_score") or {}) if critic else {}
    breakdown = depth.get("breakdown") or {}
    # "quantitative_evidence" only appears in breakdown for numeric-heavy
    # questions (see coverage.py) — for those, replace the pre-report raw
    # candidate-count estimate with what actually survived verification in
    # the final printed section, so the confidence card can't say "67%" over
    # a table that reads "No numeric results could be verified".
    quant_pct = (
        _quantitative_evidence_pct_from_section(quant)
        if depth.get("quantitative_evidence")  # {} for non-numeric questions, falsy
        else None
    )
    if quant_pct == 0 and "vendor_reported_numbers_hardened" not in flags:
        body, harden_flags = harden_unverified_numeric_claims(body, quant_absent=True)
        flags.extend(harden_flags)
        hardened_rule = _section(body, "Decision rule")
        if hardened_rule:
            decision_rule = hardened_rule

    # Must run after every step above that can touch "## Decision rule"
    # (_rewrite_decision_rule_section x2, harden_unverified_numeric_claims) --
    # several of them can leave a "no evidence-backed threshold" disclaimer
    # sentence stranded outside ### Empirical cutoffs, or leave that
    # subsection's own "Act on these" announcement with nothing under it.
    body = _fix_empty_empirical_cutoffs(body)
    # report.py re-stitches "## Decision rule" from the returned `decision_rule`
    # string right after this, independent of body_markdown -- leaving it
    # stale here would silently undo the fix just applied to body.
    fixed_rule = _section(body, "Decision rule")
    if fixed_rule:
        decision_rule = fixed_rule

    body = _fold_verification_failures_into_body(body, limitations)

    confidence_breakdown = {
        "score": _corrected_score(depth, quant_pct),
        "label": depth.get("label"),
        "must_answer_pct": (depth.get("must_answer") or {}).get("pct"),
        "critical_pct": (depth.get("critical") or {}).get("pct"),
        "primary_sources_pct": (depth.get("primary_sources") or {}).get("pct"),
        "cross_validation_pct": (depth.get("cross_validation") or {}).get("pct"),
        "implementation_pct": (depth.get("implementation") or {}).get("pct"),
        "quantitative_evidence_pct": quant_pct,
        "components": breakdown,
    }

    return {
        "body_markdown": body,
        "decision_rule": decision_rule,
        "at_a_glance": at_a_glance,
        "limitations": _dedupe_limitations(limitations),
        "integrity_flags": flags,
        "confidence_breakdown": confidence_breakdown,
        "integrity_issues": audit_notes,
    }


# Same weight coverage.py._research_quality gives quant_pct in its overall
# score formula — kept here only to correct that one term post-report.
_QUANT_SCORE_WEIGHT = 0.10


def _corrected_score(depth: dict, quant_pct: int | None) -> int | None:
    """Re-weight the pre-report overall score with the real post-write quant
    signal, so a table that ended up empty can't still show a 90+ headline."""
    score = depth.get("score")
    if score is None or quant_pct is None:
        return score
    stale_quant_pct = (depth.get("quantitative_evidence") or {}).get("pct")
    if stale_quant_pct is None:
        return score
    corrected = score - _QUANT_SCORE_WEIGHT * (stale_quant_pct - quant_pct)
    return max(0, min(100, int(round(corrected))))


def _quantitative_evidence_pct_from_section(section: str) -> int:
    """Post-write signal: how many measured figures actually survived into the
    final Quantitative findings section, after sanitize_quantitative_table
    dropped anything not traceable to the cited source. The pre-report
    depth-score estimate counts raw candidate fragments in the collected
    evidence, which can look confident even when every candidate later fails
    verification and the printed table ends up empty — this is the number
    that should actually reach the UI."""
    if _quantitative_data_absent(section):
        return 0
    measured = len(
        re.findall(
            r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:ms|µs|s\b|FLOP|TFLOP|GFLOP|tok(?:ens)?/s|GB/s)",
            section,
            re.I,
        )
    )
    return min(100, round(100 * measured / 3))


def _quantitative_data_absent(section: str) -> bool:
    if not (section or "").strip():
        return True
    if QUANT_ABSENT_RE.search(section):
        return True
    measured = len(
        re.findall(
            r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:ms|µs|s\b|FLOP|TFLOP|GFLOP|tok(?:ens)?/s|GB/s)",
            section,
            re.I,
        )
    )
    not_reported = len(re.findall(r"\bnot\s+reported\b", section, re.I))
    return measured == 0 or not_reported >= max(2, measured)


def _uncited_load_bearing_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for line in (text or "").splitlines():
        if CITE_RE.search(line):
            continue
        for match in LOAD_NUMBER_RE.finditer(line):
            raw = match.group(1).replace(",", "")
            if raw.isdigit() and len(raw) < 3:
                continue
            if raw.isdigit() and 1900 <= int(raw) <= 2035:
                continue
            out.add(match.group(0).strip())
    return out


def _strip_protected_sections(text: str) -> str:
    """Drop ## Source quality / ## References content.

    Both sections deliberately merge every citation for a band/entry into
    one bracket group (RACE criteria: "merge all cites for that band into
    one bracket group") -- scanning them for citation-stacking or source-
    concentration would flag the memo's own correctly-designed summary
    sections as if they were a prose problem.
    """
    sections = re.split(r"(^##\s+.+$)", text or "", flags=re.M)
    out_parts: list[str] = []
    in_protected = False
    for part in sections:
        if re.match(r"^##\s+", part):
            section_name = re.sub(r"^##\s+", "", part).strip()
            in_protected = any(protected in section_name for protected in PROTECTED_SECTIONS)
            continue
        if not in_protected:
            out_parts.append(part)
    return "\n".join(out_parts)


def _citation_stacks(text: str) -> list[str]:
    stacks: list[str] = []
    for line in _strip_protected_sections(text).splitlines():
        cites = CITE_RE.findall(line)
        if not cites:
            continue
        total = sum(len(c.split(",")) for c in cites)
        if total >= 3:
            stacks.append(cites[0])
    return stacks


def _as_cite_tier(citation: dict | object) -> str:
    if isinstance(citation, dict):
        raw = citation.get("tier")
    else:
        raw = getattr(citation, "tier", None)
    return str(raw or "unknown").lower()


def _load_bearing_source_concentration(
    blob: str,
    citations: list[dict] | None,
    executive_summary: str,
    at_a_glance: str,
) -> str:
    """A single low-tier ("unknown") citation carrying the memo's most
    prominent claims alone, with no independent higher-tier source ever
    corroborating it, is a real credibility risk the confidence card's
    "Primary sources" percentage doesn't catch -- that metric only checks
    whether SOME primary-tier source exists anywhere in the working set, not
    whether the sources readers actually weight most (the executive summary,
    at-a-glance line, and key findings) rest on one unverified page.

    Real run: an "UNKNOWN"-tier personal blog was the sole citation for the
    Executive summary's opening sentence and most of Key findings, while the
    memo's only peer/primary sources sat unused for the claims that matter.
    """
    if not citations:
        return ""
    low_tier_ns = {
        n for c in citations
        if (n := _as_cite_n(c)) is not None and _as_cite_tier(c) in {"unknown", ""}
    }
    if not low_tier_ns:
        return ""

    exec_section = _section(blob, "Executive summary") or executive_summary or ""
    # Executive summary / Key findings are prose paragraphs -- several
    # sentences, each with its own citation, sit on one markdown line with
    # no line break between them. Counting by markdown line (as
    # _citation_stacks does) unions every citation on the paragraph into one
    # set and never finds a "sole" citation; splitting into sentences is
    # what actually recovers per-claim attribution.
    lead_sentences = split_sentences(exec_section)
    lead_sentence = lead_sentences[0].strip() if lead_sentences else ""
    # Detailed analysis is where most of a memo's actual claims live --
    # Executive summary/Key findings alone are short enough that a real
    # concentration problem can still stay under threshold (real run: source
    # [1] hit 5 sole-cited sentences once Detailed analysis was included,
    # vs. only 2 across Executive summary + Key findings by themselves).
    load_bearing_text = "\n".join(
        s
        for s in (
            at_a_glance,
            exec_section,
            _section(blob, "Key findings"),
            _section(blob, "Detailed analysis"),
        )
        if s
    )
    if not load_bearing_text.strip():
        return ""

    sole_counts: dict[int, int] = {}
    lead_n: int | None = None
    for sentence in split_sentences(load_bearing_text):
        if not sentence.strip():
            continue
        nums: set[int] = set()
        for group in CITE_RE.findall(sentence):
            for piece in group.split(","):
                m = re.match(r"\s*(\d+)", piece)
                if m:
                    nums.add(int(m.group(1)))
        if len(nums) != 1:
            continue
        n = next(iter(nums))
        sole_counts[n] = sole_counts.get(n, 0) + 1
        if lead_n is None and sentence.strip() == lead_sentence:
            lead_n = n

    # A single claim resting on one unranked source is unremarkable -- one
    # citation is how prose normally works. Only flag genuine repetition:
    # the same low-tier source carrying several load-bearing claims alone.
    # lead_n only sharpens the message when the concentrated source is also
    # the one thing backing the memo's very first sentence.
    candidates = [
        (n, count) for n, count in sole_counts.items()
        if n in low_tier_ns and count >= 3
    ]
    if not candidates:
        return ""
    n, count = max(candidates, key=lambda item: item[1])
    lead_note = " (including the Executive summary's opening claim)" if n == lead_n else ""
    return (
        f"Source concentration: [{n}] is an unranked/unverified source, yet it is the sole "
        f"citation for {count} statement(s) across Executive summary, Key findings, and "
        f"Detailed analysis{lead_note} — no higher-tier source corroborates it independently."
    )


def _normalize_prose(text: str) -> str:
    t = re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", text or "")
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _tone_mismatch(text: str) -> str:
    exec_s = _section(text, "Executive summary").lower()
    contra = _section(text, "Contradictions & debates").lower()
    if not exec_s or not contra:
        return ""
    strong = bool(re.search(r"\b(resolves|proves|definitively|clearly shows)\b", exec_s))
    hedged = bool(
        re.search(r"\b(divided|fragile|uncertain|remains open|insufficient)\b", contra)
    )
    if strong and hedged:
        return (
            "Tone mismatch: Executive summary sounds definitive while Contradictions "
            "admits division — soften lead claims or elevate the caveat in At a glance."
        )
    return ""


_THRESHOLD_DISCLAIMER_RE = re.compile(
    r"[^.\n]*\bevidence[- ]backed threshold[^.\n]*\.\s*", re.I
)


_UNVERIFIED_NUM_RE = re.compile(
    r"^(.+?)\s+cited to\s+(\[[^\n]*?\])\s+but not found in those sources\.?$", re.I
)
_WEAK_DIMENSION_RE = re.compile(r"remain weak or unverified|could not be verified", re.I)
_NAMED_SUBJECT_GAP_RE = re.compile(
    r"^Named subjects with no dedicated evidence:\s*(.+?)\.?$", re.I
)
_CITATION_STACKING_RE = re.compile(
    r"^Citation stacking:\s*(\d+)\s+sentence\(s\)\s+attach 3\+ sources at once\s+\(e\.g\.\s+\[([^\]]+)\]\)", re.I
)
_INFO_DENSITY_RE = re.compile(r"^Information density:", re.I)
_SOURCE_CONCENTRATION_RE = re.compile(r"^Source concentration:", re.I)


def _reader_facing_limitations(limitations: list[str]) -> list[str]:
    """The subset of computed limitations the memo's reader must actually see.

    Most entries are process boilerplate (host counts, snippet-retrieval
    caveats) that the writer's own Limitations prose already implies. The
    families below are different in kind: the system reporting that
    something it just printed does not hold up — a figure attributed to a
    source that does not contain it, a critical dimension that never got
    covered, several claims bundled behind one citation stack, the same
    theme padded across sections, or the memo's central claims resting on
    one unranked source with nothing else to check it against. These were
    being computed, stored in metrics, and shown to nobody (real run: "100x
    cited to [1] but not found in those sources" while the memo printed "up
    to 100x ... [1]" four times, unhedged).
    """
    grouped: dict[str, list[str]] = {}
    out: list[str] = []
    # audit_memo_integrity() runs at several pipeline stages on progressively
    # edited bodies, so citation-stacking gets measured more than once --
    # real run showed three near-identical "N statement(s) cite 3+ sources"
    # bullets (15, 9, 8) back to back. Keep only the latest measurement
    # (the one closest to the body actually published), not all of them.
    best_stack: tuple[int, str] | None = None
    for item in limitations:
        text = (item or "").strip()
        match = _UNVERIFIED_NUM_RE.match(text)
        if match:
            grouped.setdefault(match.group(2), []).append(match.group(1).strip())
            continue
        gap = _NAMED_SUBJECT_GAP_RE.match(text)
        if gap:
            out.append(
                f"Asked about but never covered by its own evidence: {gap.group(1)}. "
                "Statements about it are inferred from the other sources, not sourced directly."
            )
            continue
        stack = _CITATION_STACKING_RE.match(text)
        if stack:
            best_stack = (int(stack.group(1)), stack.group(2))
            continue
        if _INFO_DENSITY_RE.match(text):
            # The underlying check's message hardcodes unrelated placeholder
            # examples ("sandbox filtering, model collapse") that have
            # nothing to do with this specific memo -- forward the finding,
            # not its fake specifics.
            out.append(
                "Some themes repeat across multiple sections without adding new facts — "
                "later mentions may restate an earlier claim rather than extend it."
            )
            continue
        if _SOURCE_CONCENTRATION_RE.match(text):
            out.append(text)
            continue
        if _WEAK_DIMENSION_RE.search(text):
            out.append(text)
    if best_stack is not None:
        count, example = best_stack
        out.append(
            f"{count} statement(s) in this memo cite 3 or more sources at once "
            f"(e.g. [{example}]) — treat these as one bundled claim; the sources were not "
            "checked individually against it."
        )
    # Different lines (e.g. a paragraph and a comparison table restating the
    # same figure) can flag the identical set of numbers under different --
    # sometimes overlapping -- citation groups. Merge by number-set so the
    # reader sees one bullet per distinct unverified figure, not the same
    # "8x, 16x, 0.95" claim twice with slightly different citations attached.
    merged: dict[tuple[str, ...], list[str]] = {}
    for cites, nums in grouped.items():
        key = tuple(dict.fromkeys(nums))
        bucket = merged.setdefault(key, [])
        for token in re.findall(r"\[[^\]]+\]", cites):
            if token not in bucket:
                bucket.append(token)
    for nums_key, cite_tokens in merged.items():
        joined = ", ".join(nums_key)
        out.append(
            f"Unverified attribution: {joined} cited to {', '.join(cite_tokens)}, but the cited "
            "source text does not contain the figure — treat as unsourced until re-checked."
        )
    return out


def _fold_verification_failures_into_body(body: str, limitations: list[str]) -> str:
    """Put reader-facing verification failures under ## Limitations.

    The writer composes its own Limitations prose from the notes it was
    given and never sees the post-write audit, so without this the audit's
    findings only reach metrics_json.
    """
    notes = [n for n in _reader_facing_limitations(limitations) if n not in (body or "")]
    if not notes:
        return body
    block = "\n".join(f"- {n}" for n in notes[:6])
    heading = re.search(r"^##\s+Limitations\s*$", body or "", re.I | re.M)
    if not heading:
        return f"{(body or '').rstrip()}\n\n## Limitations\n\n{block}\n"
    return body[: heading.end()] + f"\n\n{block}" + body[heading.end() :]


_ACT_ON_THESE_STUB_RE = re.compile(r"^\**Act on these[^\n]*\**\s*\n+", re.I | re.M)


def _fix_empty_empirical_cutoffs(body: str) -> str:
    """Guarantee ### Empirical cutoffs never reads as a promise with nothing
    under it, and that a "no evidence-backed threshold" disclaimer — when one
    is needed — sits inside that subsection instead of wherever the writer or
    an upstream sanitizer happened to drop it.

    Multiple steps can strip Empirical cutoffs' bullets after the section's
    own "Act on these" announcement was already chosen (each per-source
    threshold turned out to be uncited, or unverifiable, and got dropped one
    at a time), leaving that announcement orphaned. And the disclaimer
    sentence itself has shown up appended to the very end of Decision rule —
    after Engineering heuristics — where "use Engineering heuristics below"
    points at nothing (real run: literal last line of the section). This
    dedupes every occurrence and puts exactly one back in the right place.
    """
    match = re.search(r"^##\s+Decision rule\s*$", body, re.I | re.M)
    if not match:
        return body
    start = match.end()
    rest = body[start:]
    nxt = re.search(r"^##\s+", rest, re.M)
    end = start + (nxt.start() if nxt else len(rest))
    section = body[start:end]

    empirical = re.search(r"^###\s+Empirical cutoffs[^\n]*\n", section, re.I | re.M)
    if not empirical:
        return body
    next_heading = re.search(r"^###\s+", section[empirical.end():], re.M)
    empirical_body_end = empirical.end() + (next_heading.start() if next_heading else len(section) - empirical.end())
    empirical_body = section[empirical.end():empirical_body_end]

    has_real_cutoff = bool(re.search(r"\[\s*\d", empirical_body))
    has_bullet = bool(re.search(r"^\s*[-*]\s+\S", _THRESHOLD_DISCLAIMER_RE.sub("", empirical_body), re.M))

    new_section = _THRESHOLD_DISCLAIMER_RE.sub("", section)
    if has_real_cutoff or has_bullet:
        # Real content speaks for itself; any disclaimer elsewhere was noise.
        pass
    else:
        empirical2 = re.search(r"^###\s+Empirical cutoffs[^\n]*\n", new_section, re.I | re.M)
        insert_at = empirical2.end()
        after = _ACT_ON_THESE_STUB_RE.sub("", new_section[insert_at:], count=1)
        new_section = (
            new_section[:insert_at]
            + "\n*Evidence-backed threshold: none in collected sources.*\n\n"
            + after
        )
    if new_section == section:
        return body
    return body[:start] + new_section + body[end:]


def _label_illustrative_section(body: str, heading: str) -> str:
    """Label when the section states load-bearing numbers with no citation at all."""
    section = _section(body, heading)
    if not section or not _uncited_load_bearing_numbers(section):
        return body
    return _insert_illustrative_prefix(body, heading)


def _insert_illustrative_prefix(body: str, heading: str) -> str:
    section = _section(body, heading)
    if not section or ILLUSTRATIVE_PREFIX.strip() in section:
        return body
    pattern = re.compile(rf"(^##\s+{re.escape(heading)}\s*$)", re.I | re.M)
    match = pattern.search(body)
    if not match:
        return body
    start = match.end()
    rest = body[start:]
    nxt = re.search(r"^##\s+", rest, re.M)
    end = start + (nxt.start() if nxt else len(rest))
    old = body[start:end]
    if old.lstrip().startswith(">"):
        return body
    # Once a section is labeled "illustrative / not measured", no [n] marker
    # in it should survive — a reader sees a live citation next to a number
    # and assumes it's backed by that source regardless of a disclaimer
    # above it. Real memo output kept [3]/[3 specialist] on both the genuine
    # figures AND the invented ones in the same passage, making it
    # impossible to tell which numbers were real without checking the paper.
    no_cites = CITE_RE.sub("", old.lstrip())
    no_cites = re.sub(r"[ \t]+([.,;:])", r"\1", no_cites)
    no_cites = re.sub(r"[ \t]{2,}", " ", no_cites)
    new = "\n\n" + ILLUSTRATIVE_PREFIX + no_cites
    return body[:start] + new + body[end:]


def _worked_example_mostly_ungrounded(
    section: str, *, citations: list[dict], evidence: list[dict]
) -> bool:
    """True when most cited numeric claims in Worked Example can't be traced
    to the source they cite — a confident [n] next to an invented number is
    worse than an honest 'illustrative' label."""
    from app.domain.quantitative_verify import _evidence_blob, number_in_source

    by_n = {int(c["n"]): c for c in citations if c.get("n") is not None}
    by_url = {
        (ev.get("url") or "").strip().rstrip("/").lower(): ev for ev in evidence if ev.get("url")
    }
    claims = 0
    grounded = 0
    for line in (section or "").splitlines():
        cite_matches = CITE_RE.findall(line)
        if not cite_matches:
            continue
        # LOAD_NUMBER_RE skips bare numbers under 3 digits (built for
        # token-count claims like "5,700 tokens"), so on its own it can't
        # catch an invented two-digit percentage — check those too.
        numbers = list(
            dict.fromkeys(
                [m.group(0) for m in re.finditer(r"\d+(?:\.\d+)?%", line)]
                + [
                    m.group(0)
                    for m in LOAD_NUMBER_RE.finditer(line)
                    if not (m.group(1).replace(",", "").isdigit() and len(m.group(1).replace(",", "")) < 3)
                ]
            )
        )
        if not numbers:
            continue
        first_piece = cite_matches[-1].split(",")[0].strip()
        digits = re.match(r"\d+", first_piece)
        if not digits:
            continue
        cite_n = int(digits.group(0))
        cite = by_n.get(cite_n) or {}
        url = (cite.get("url") or "").strip().rstrip("/").lower()
        ev = by_url.get(url) or {}
        blob = _evidence_blob(ev) or _evidence_blob(cite)
        if len(blob.strip()) < 25:
            continue
        for token in numbers:
            claims += 1
            if number_in_source(token, blob):
                grounded += 1
    if claims < 2:
        return False
    return grounded / claims < 0.5


def _strip_ungrounded_decision_numbers(
    rule: str, *, citations: list[dict], evidence: list[dict]
) -> str:
    """Drop any Decision rule line whose cited numeric claim doesn't verify
    against the source it names. Decision rule is prescriptive, action-you-
    should-take text — a confidently-cited but wrong number there is worse
    than a missing one, and unlike Worked Example there's no softer
    'illustrative' framing that fits an instruction to actually do something."""
    if not (rule or "").strip() or not citations:
        return rule or ""
    from app.domain.quantitative_verify import _evidence_blob, number_in_source

    by_n = {int(c["n"]): c for c in citations if c.get("n") is not None}
    by_url = {
        (ev.get("url") or "").strip().rstrip("/").lower(): ev for ev in (evidence or []) if ev.get("url")
    }
    out_lines: list[str] = []
    for line in (rule or "").splitlines():
        cite_matches = CITE_RE.findall(line)
        # LOAD_NUMBER_RE (shared with _worked_example_mostly_ungrounded) skips
        # bare numbers under 3 digits — built for token-count claims like
        # "5,700 tokens", so it never flags a two-digit percentage like the
        # "76%" in the real bug this guards against. Check percentages too.
        numbers = list(
            dict.fromkeys(
                [m.group(0) for m in re.finditer(r"\d+(?:\.\d+)?%", line)]
                + [
                    m.group(0)
                    for m in LOAD_NUMBER_RE.finditer(line)
                    if not (m.group(1).replace(",", "").isdigit() and len(m.group(1).replace(",", "")) < 3)
                ]
            )
        )
        if not cite_matches or not numbers:
            out_lines.append(line)
            continue
        first_piece = cite_matches[-1].split(",")[0].strip()
        digits = re.match(r"\d+", first_piece)
        if not digits:
            out_lines.append(line)
            continue
        cite_n = int(digits.group(0))
        cite = by_n.get(cite_n) or {}
        url = (cite.get("url") or "").strip().rstrip("/").lower()
        ev = by_url.get(url) or {}
        blob = _evidence_blob(ev) or _evidence_blob(cite)
        if len(blob.strip()) < 25:
            out_lines.append(line)
            continue
        if any(not number_in_source(tok, blob) for tok in numbers):
            continue
        out_lines.append(line)
    return _renumber_ordered_list("\n".join(out_lines))


_ORDERED_ITEM_RE = re.compile(r"^(\d+)\.(\s+\S.*)$")


def _renumber_ordered_list(text: str) -> str:
    """Close gaps left by a dropped line — a decision rule that jumps
    "1. ... 3. ... 4." (2 silently removed) reads as broken, not as a
    successfully-caught bad claim."""
    counter = 0
    out = []
    for line in text.splitlines():
        m = _ORDERED_ITEM_RE.match(line)
        if m:
            counter += 1
            out.append(f"{counter}.{m.group(2)}")
        else:
            out.append(line)
    return "\n".join(out)



def _as_cite_n(citation: dict | object) -> int | None:
    if isinstance(citation, dict):
        raw = citation.get("n")
    else:
        raw = getattr(citation, "n", None)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _strip_cite_markers_outside_protected(body: str, numbers: list[int]) -> str:
    """Remove [n] / [n tier] markers from prose only.

    Skips ## Source quality and ## References so list markers stay intact.
    Also scrubs residual **** left if a bold marker was emptied upstream.
    """
    if not body or not numbers:
        return body or ""
    nums = sorted({int(n) for n in numbers})
    patterns = [re.compile(rf"\[{n}(?:\s+[A-Za-z]+)?\]") for n in nums]

    sections = re.split(r"(^##\s+.+$)", body, flags=re.M)
    out_parts: list[str] = []
    in_protected = False
    for part in sections:
        if re.match(r"^##\s+", part):
            section_name = re.sub(r"^##\s+", "", part).strip()
            in_protected = any(protected in section_name for protected in PROTECTED_SECTIONS)
            out_parts.append(part)
            continue
        if in_protected:
            out_parts.append(part)
            continue
        chunk = part
        for pat in patterns:
            chunk = pat.sub("", chunk)
        chunk = chunk.replace("****", "")
        chunk = re.sub(r"[ \t]{2,}", " ", chunk)
        out_parts.append(chunk)
    return "".join(out_parts)


def _strip_ungrounded_entity_citations(
    body: str, *, citations: list[dict], evidence: list[dict], query: str
) -> tuple[str, int]:
    """A descriptive line about a named framework/product can carry a
    citation whose source never actually discusses that name — the writer
    filled in the description from its own training data and attached a
    plausible-looking [n] instead of what it actually retrieved. Real case:
    a memo's LangGraph/AutoGen/CrewAI capability descriptions were cited to
    papers that never mention any of the three (grepped the raw evidence
    text directly). Strip a citation that doesn't hold up for the entity
    the line is actually about, rather than let a false source stand.
    
    Skips PROTECTED_SECTIONS (Source quality, References) where citation
    lists should remain intact to prevent ****** in reference entries.
    """
    if not (body or "").strip() or not citations:
        return body or "", 0
    from app.domain.quantitative_verify import _evidence_blob
    from app.domain.textutil import entity_candidates, entity_pattern

    entities = [e for e in entity_candidates(user_goal(query) or query or "", limit=12) if len(e) >= 3]
    if not entities:
        return body, 0
    entity_res = [re.compile(entity_pattern(e), re.I) for e in entities]

    by_n = {int(c["n"]): c for c in citations if c.get("n") is not None}
    by_url = {
        (ev.get("url") or "").strip().rstrip("/").lower(): ev for ev in (evidence or []) if ev.get("url")
    }

    def _blob_for(cite_n: int) -> str:
        cite = by_n.get(cite_n) or {}
        url = (cite.get("url") or "").strip().rstrip("/").lower()
        ev = by_url.get(url) or {}
        return _evidence_blob(ev) or _evidence_blob(cite)
    
    # Split body at ## section headers to identify protected sections
    sections = re.split(r"(^##\s+.+$)", body, flags=re.M)
    stripped = 0
    out_parts: list[str] = []
    
    in_protected = False
    for part in sections:
        # Check if this is a section header
        if re.match(r"^##\s+", part):
            # Check if it's a protected section
            section_name = re.sub(r"^##\s+", "", part).strip()
            in_protected = any(protected in section_name for protected in PROTECTED_SECTIONS)
            out_parts.append(part)
            continue
        
        # If in protected section, skip processing
        if in_protected:
            out_parts.append(part)
            continue
        
        # Process non-protected section
        out_lines: list[str] = []
        for line in part.splitlines():
            mentioned = [pat for pat in entity_res if pat.search(line)]
            if not mentioned or not CITE_RE.search(line):
                out_lines.append(line)
                continue

            def _repl(match: re.Match) -> str:
                nonlocal stripped
                cite_ns = []
                for piece in match.group(1).split(","):
                    digits = re.match(r"\s*(\d+)", piece)
                    if digits:
                        cite_ns.append(int(digits.group(1)))
                blobs = [b for n in cite_ns if (b := _blob_for(n)).strip()]
                if not blobs or any(pat.search(b) for pat in mentioned for b in blobs):
                    return match.group(0)
                stripped += 1
                return ""

            out_lines.append(CITE_RE.sub(_repl, line).rstrip())
        # split() keeps the newline *before* the next ## header on this body
        # chunk. splitlines()+join drops that trailing newline and glues the
        # next header onto the previous line ("text## Decision rule"), which
        # then fails every ^## heading matcher and looks like the memo was
        # truncated. Always restore a trailing newline when the original chunk
        # had one.
        joined = "\n".join(out_lines)
        if part.endswith("\n") and not joined.endswith("\n"):
            joined += "\n"
        out_parts.append(joined)

    return "".join(out_parts), stripped


def _sanitize_decision_rule_numbers(rule: str, *, quant_absent: bool) -> str:
    if not quant_absent or not (rule or "").strip():
        return rule or ""
    lines: list[str] = []
    in_empirical = False
    for line in (rule or "").splitlines():
        low = line.lower()
        if "empirical cutoff" in low:
            in_empirical = True
            lines.append(line)
            continue
        if "engineering heuristic" in low:
            in_empirical = False
            lines.append(line)
            continue
        if in_empirical and THRESHOLD_IN_RULE_RE.search(line) and not CITE_RE.search(line):
            continue
        if in_empirical and re.search(r"\d+(?:\.\d+)?\s*%", line) and not CITE_RE.search(line):
            continue
        lines.append(line)
    # An empty (or now-empty) Empirical cutoffs subsection gets its
    # disclaimer filled in centrally by _fix_empty_empirical_cutoffs, which
    # runs after every Decision-rule-touching step -- inserting one here too
    # risked leaving a stale copy stranded once a later step (e.g.
    # harden_unverified_numeric_claims) rewrote the section again.
    return "\n".join(lines).strip()


def _rewrite_decision_rule_section(body: str, decision_rule: str) -> str:
    if not decision_rule.strip():
        return body
    if "## Decision rule" not in body:
        return body
    head, _, rest = body.partition("## Decision rule")
    after = rest.split("\n## ", 1)
    tail = ("\n## " + after[1]) if len(after) > 1 else ""
    return f"{head}## Decision rule\n\n{decision_rule.strip()}\n{tail}".rstrip() + "\n"


def _derive_at_a_glance(executive_summary: str, decision_rule: str, critic: dict) -> str:
    caveat = ""
    depth = (critic or {}).get("depth_score") or {}
    label = depth.get("label") or "standard"
    score = depth.get("score")
    if score is not None:
        caveat = f"Research depth {score}/100 ({label}) — treat numeric cutoffs as heuristics unless cited."
    contra = (critic or {}).get("status") == "contradicted"
    if contra:
        caveat = (caveat + " Sources disagree on key claims.").strip()

    rule_line = ""
    for line in (decision_rule or "").splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        if t.startswith("-") or t.startswith("*"):
            rule_line = re.sub(r"^[-*]\s+", "", t).strip()
            break
        if len(t) > 24 and not t.startswith("|"):
            rule_line = t
            break
    rule_line = re.sub(r"\*\*", "", rule_line)[:220]

    if rule_line and not _looks_like_diagram(rule_line):
        lead = first_sentence(rule_line) or rule_line
    else:
        lead = first_sentence(executive_summary) or "See executive summary for the full answer."

    if caveat:
        return f"{lead} {caveat}".strip()
    return lead


def first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return parts[0].strip() if parts else ""


def _looks_like_diagram(s: str) -> bool:
    return bool(re.search(r"[▼▲─│]|^\|", s or ""))



VENDOR_UNVERIFIED_LABEL = "(vendor-reported / not independently verified)"
_HARDEN_SECTIONS = ("Key findings", "Comparison", "Decision rule")
_HARD_NUM_RE = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(\s*(?:%|percent|tokens?|tok/s|ms|GB|GiB|MB|MiB|OCPU|vCPU))?(?=[\s.,;:)\]]|$)",
    re.I,
)


def harden_unverified_numeric_claims(body: str, *, quant_absent: bool) -> tuple[str, list[str]]:
    """When measured evidence is 0%, label every hard number in load-bearing sections.

    Post-write pass: Comparison / Decision rule / Key findings must not assert
    hard figures as if independently measured when the Quantitative findings
    table is empty / quant_absent.
    """
    flags: list[str] = []
    if not quant_absent or not (body or "").strip():
        return body or "", flags

    text = body
    changed_any = False
    for heading in _HARDEN_SECTIONS:
        section = _section(text, heading)
        if not section or not section.strip():
            continue
        if "vendor-reported" in section.lower() and "not independently verified" in section.lower():
            # Already hardened — still fill any unlabeled numbers.
            pass

        def _label_num(m: re.Match) -> str:
            raw = m.group(0)
            # Skip years and tiny integers without units.
            num = (m.group(1) or "").replace(",", "")
            unit = m.group(2) or ""
            if num.isdigit() and not unit and 1900 <= int(num) <= 2035:
                return raw
            if num.isdigit() and not unit and len(num) < 3:
                return raw
            # Already labeled nearby.
            window_start = max(0, m.start() - 8)
            window_end = min(len(section), m.end() + len(VENDOR_UNVERIFIED_LABEL) + 8)
            around = section[window_start:window_end]
            if "vendor-reported" in around.lower():
                return raw
            return f"{raw} {VENDOR_UNVERIFIED_LABEL}"

        new_section = _HARD_NUM_RE.sub(_label_num, section)
        if new_section != section:
            text = _rewrite_named_section(text, heading, new_section)
            changed_any = True

    if changed_any:
        flags.append("vendor_reported_numbers_hardened")
    return text, flags


def _rewrite_named_section(body: str, heading: str, new_body: str) -> str:
    pattern = re.compile(rf"(^##\s+{re.escape(heading)}\s*$)", re.I | re.M)
    match = pattern.search(body or "")
    if not match:
        return body
    start = match.end()
    rest = body[start:]
    nxt = re.search(r"^##\s+", rest, re.M)
    return (
        body[:start]
        + "\n\n"
        + new_body.strip()
        + "\n\n"
        + (rest[nxt.start() :] if nxt else "")
    )


def integrity_severity(issues: list[str], body: str) -> str:
    """ok | warn | critical — critical triggers planner re-loop."""
    quant = _section(body or "", "Quantitative findings")
    quant_absent = _quantitative_data_absent(quant)
    has_contradiction = any("internal contradiction" in (i or "").lower() for i in issues)
    if quant_absent and has_contradiction:
        return "critical"
    if issues:
        return "warn"
    return "ok"


def build_integrity_reloop_followups(query: str, issues: list[str] | None = None) -> list[dict]:
    from app.graph.serde import dump  # lazy: avoid pulling graph/psycopg at import time
    goal = user_goal(query or "")
    rationale = "Integrity re-loop: quantitative contradiction detected"
    if issues:
        rationale = f"{rationale} — {issues[0][:120]}"
    return [
        dump(
            SubQuery(
                agent=AgentName.SEARCH,
                question=f"Measured benchmarks with exact numbers (% latency FLOPs tokens) for: {goal}",
                rationale=rationale,
            )
        ),
        dump(
            SubQuery(
                agent=AgentName.SCHOLAR,
                question=f"Peer-reviewed quantitative evaluation with reported metrics for: {goal}",
                rationale="Integrity re-loop: fill measured evidence gaps",
            )
        ),
    ]


def _dedupe_limitations(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = (item or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out[:20]


def _insert_prefix_to_section(body: str, section_name: str, prefix: str) -> str:
    """Insert a prefix (like Composite label) at the start of a section's content."""
    pattern = re.compile(rf"(##\s+{re.escape(section_name)}\s*\n)", re.I)
    match = pattern.search(body)
    if not match:
        return body
    
    insert_pos = match.end()
    return body[:insert_pos] + prefix + body[insert_pos:]


def _drop_empty_sections(body: str) -> str:
    """Drop sections with < 3 content lines and no citations.
    
    Prevents publishing headings like '## Worked example' with only intro text
    but no actual content, which happens when regeneration loops strip content
    to pass checks.
    """
    sections = re.split(r"(^##\s+.+$)", body, flags=re.M)
    out_parts: list[str] = []
    
    i = 0
    while i < len(sections):
        part = sections[i]
        
        # If this is a section header
        if re.match(r"^##\s+", part):
            # Look at the next part (section content)
            if i + 1 < len(sections):
                content = sections[i + 1]
                
                # Count non-empty lines
                content_lines = [ln for ln in content.splitlines() if ln.strip()]
                has_citations = CITE_RE.search(content)
                # A section just labeled "Illustrative scenario" / "Composite"
                # by the checks above is deliberately short and uncited —
                # that's the whole point of the caveat, not a sign it should
                # be dropped as filler.
                is_caveated = "illustrative scenario" in content.lower() or "> **composite**" in content.lower()

                # Drop if too short and no citations
                if len(content_lines) < 3 and not has_citations and not is_caveated:
                    i += 2  # Skip both header and content
                    continue
            
            # Keep this section
            out_parts.append(part)
            if i + 1 < len(sections):
                out_parts.append(sections[i + 1])
            i += 2
        else:
            # Non-header part (like intro before first ##)
            out_parts.append(part)
            i += 1
    
    return "".join(out_parts)


def audit_body_numbers(
    body: str,
    *,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> list[str]:
    """Verify every number + citation pair in memo body, not just Quantitative table.
    
    Catches misattributions like '16ms [7 specialist]' where [7] doesn't mention 16ms.
    Runs on ALL sections (prose, Comparison table, Worked example) to close the gap
    where audit_quantitative_table silently no-ops when section uses bullet format.
    
    Returns list of issues (empty if all numbers are grounded).
    """
    from app.domain.quantitative_verify import _row_numbers, number_in_source
    
    issues: list[str] = []
    citations = citations or []
    evidence = evidence or []
    
    # Build citation → evidence blob lookup
    by_n: dict[int, str] = {}
    for cit in citations:
        n = cit.get("n")
        url = cit.get("url", "")
        if not n:
            continue
        # Find evidence blob for this citation
        for ev in evidence:
            if ev.get("url") == url:
                blob = f"{ev.get('title', '')} {ev.get('snippet', '')} {ev.get('quote', '')} {ev.get('text', '')}"
                by_n[n] = blob
                break
    
    # Extract citation numbers from text like [7 specialist] → [7]
    def _cite_nums(text: str) -> list[int]:
        nums: list[int] = []
        for m in CITE_RE.finditer(text):
            parts = m.group(1).split(",")
            for p in parts:
                digits = re.search(r"\d+", p.strip())
                if digits:
                    nums.append(int(digits.group(0)))
        return nums
    
    # Scan every line with both numbers and citations
    for line in (body or "").splitlines():
        if not line.strip() or "[" not in line:
            continue
        
        numbers = _row_numbers(line)
        cite_ns = _cite_nums(line)
        
        if not numbers or not cite_ns:
            continue
        
        # For each number in this line, check if ANY cited source contains it
        for num in numbers:
            found_in_any = False
            for n in cite_ns:
                blob = by_n.get(n, "")
                if blob and number_in_source(num, blob):
                    found_in_any = True
                    break
            
            if not found_in_any and cite_ns:
                # Number present, citations present, but number not in any cited source.
                # cite_ns can repeat the same source across multiple bracket groups on
                # one line (e.g. "[2] ... [4] ... [2]") -- dedupe before joining so the
                # reader-facing message never reads "cited to [2], [4], [2]".
                cite_str = ", ".join(f"[{n}]" for n in dict.fromkeys(cite_ns[:3]))
                issues.append(f"{num} cited to {cite_str} but not found in those sources")
    
    return issues
