"""Post-draft constraint audit against ResearchContract (Kiln Phase B).

Fail-soft: if no contract, callers skip. Calls demote_off_scope_memo_content;
does not strip [n] inside References / Source quality.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.metric_grounding import (
    demote_off_scope_memo_content,
    extract_model_scales,
    is_memory_footprint_query,
)
from app.domain.research_contract import (
    ResearchContract,
    mandatory_sources_present,
)

UNCERTAINTIES_HEADING = "## Uncertainties & gaps"
UNCERTAINTIES_ALIASES = (
    r"Uncertainties\s*&\s*gaps",
    r"What we don't know yet",
)

# Author / official-repo identity hints for LoRA & QLoRA primaries.
AUTHOR_REPO_IDENTITY = (
    {
        "topic": "qlora",
        "topic_re": re.compile(r"\bQLoRA\b", re.I),
        "good_authors": ("dettmers", "tim dettmers"),
        "good_repos": ("artidoro/qlora", "timdettmers/bitsandbytes", "bitsandbytes"),
        "bad_misattr": re.compile(
            r"\b(?:QLoRA)\b.{0,80}\b(?:invented|proposed|introduced)\s+by\s+(?!Tim\s+Dettmers)([A-Z][a-z]+)",
            re.I,
        ),
    },
    {
        "topic": "lora",
        "topic_re": re.compile(r"\bLoRA\b(?!\s*vs)", re.I),
        "good_authors": ("edward j. hu", "e. j. hu", "hu et al", "microsoft/lora"),
        "good_repos": ("microsoft/lora", "microsoft/LoRA"),
        "bad_misattr": None,
    },
)

EXCLUDE_HIT_RE = {
    "DPO": re.compile(r"\bDPO\b"),
    "ORPO": re.compile(r"\bORPO\b"),
    "KTO": re.compile(r"\bKTO\b"),
    "RLHF": re.compile(r"\bRLHF\b"),
    "clinical": re.compile(r"\b(?:clinical\s+text|mental[- ]health|psychiatric)\b", re.I),
    "mental-health": re.compile(r"\bmental[- ]health\b", re.I),
    "email-QA": re.compile(r"\bemail\s+(?:QA|question\s+answering)\b", re.I),
    "abstention": re.compile(r"\babstention(?:\s+accuracy)?\b", re.I),
}

SECTION_SPLIT_RE = re.compile(r"(^##\s+.+$)", re.M)
CITE_RE = re.compile(r"\[(\d+(?:\s+[A-Za-z]+)?(?:\s*,\s*\d+(?:\s+[A-Za-z]+)?)*)\]")


def _as_contract(contract: ResearchContract | dict | None) -> ResearchContract | None:
    if isinstance(contract, ResearchContract):
        return contract
    return ResearchContract.from_dict(contract if isinstance(contract, dict) else None)


def _section(text: str, name: str) -> str | None:
    pattern = re.compile(
        rf"^##\s+{re.escape(name)}\s*$",
        re.I | re.M,
    )
    m = pattern.search(text or "")
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^##\s+", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    return text[start:end].strip()



def audit_note_to_measurement_gap_prose(note: str) -> str:
    """Rewrite machine/audit flags into Measurement-gaps voice for the memo.

    All user-visible Uncertainties bullets must pass through this helper —
    never dump raw flag ids like ``mandatory_missing_2305.14314``.
    """
    raw = (note or "").strip()
    if not raw:
        return ""
    low = raw.lower()

    # Already prose-like measurement gap — keep.
    if raw.startswith(("- ", "* ")):
        raw = raw[2:].strip()

    # Strip raw flag-id shape: snake_case tokens with no spaces.
    if re.fullmatch(r"[a-z][a-z0-9_.]{3,100}", low) and "_" in low:
        pretty = low.replace("_", " ").replace("hard fail ", "").strip()
        if pretty.startswith("mandatory missing"):
            rest = pretty[len("mandatory missing"):].strip(" :.")
            return (
                f"Primary reference not yet in the citation ledger: {rest}. "
                "Until it is retrieved, treat related claims as provisional."
            )
        return (
            f"Open measurement gap: {pretty}. Collect a primary source that "
            "closes this before treating related claims as settled."
        )

    if "mandatory source missing" in low:
        # "Mandatory source missing from citations/evidence: Label (id)."
        m = re.search(
            r"mandatory source missing from citations/evidence:\s*(.+?)(?:\.|$)",
            raw,
            re.I,
        )
        label = (m.group(1).strip() if m else raw)
        label = re.sub(r"^mandatory source missing[^:]*:\s*", "", label, flags=re.I)
        return (
            f"Primary reference not yet in the citation ledger: {label.rstrip('.')}. "
            "Until it is retrieved, treat related memory/method claims as provisional."
        )

    if "excluded domain" in low:
        m = re.search(r"excluded domain\s+'([^']+)'", raw, re.I)
        dom = m.group(1) if m else "out-of-scope topic"
        return (
            f"Collected excerpts still lean on {dom}, which is out of scope for this "
            "question — do not treat those passages as load-bearing evidence."
        )

    if "scale mismatch" in low:
        return (
            "Model-scale figures in the draft may not match the scale asked in the "
            "question. Keep each number scoped to the model size that was actually measured."
        )

    if "qlora memory claims lack" in low or "dettmers" in low and "identity" in low:
        return (
            "QLoRA memory figures are not yet anchored to the Tim Dettmers / "
            "artidoro/qlora (or bitsandbytes) primaries — prefer those before locking VRAM claims."
        )

    if "lora primary identity" in low or ("hu et" in low and "lora" in low):
        return (
            "LoRA method claims are not yet anchored to Hu et al. / microsoft/LoRA — "
            "prefer those primaries before treating adapter details as settled."
        )

    if "mis-attribution" in low or "misattribution" in low:
        return (
            "Authorship for a core method may be mis-attributed in the draft. "
            "Verify against Hu 2106.09685 / Dettmers 2305.14314 before publishing."
        )

    # Soft scrub of audit jargon while keeping meaning.
    cleaned = re.sub(r"\b(flag|audit|hard_fail|should_block_publish)\b", "", raw, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -—:")
    if not cleaned:
        cleaned = raw
    if cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    if not cleaned.endswith("."):
        cleaned += "."
    return cleaned


def _replace_or_insert_uncertainties(body: str, bullets: list[str]) -> str:
    """Append gap bullets under Uncertainties & gaps (create section if missing)."""
    if not bullets:
        return body or ""
    text = body or ""
    prose_bullets = [audit_note_to_measurement_gap_prose(b) for b in bullets]
    prose_bullets = [b for b in prose_bullets if b]
    if not prose_bullets:
        return text
    block = "\n".join(f"- {b}" for b in prose_bullets)

    for alias in ("Uncertainties & gaps", "What we don't know yet"):
        existing = _section(text, alias)
        if existing is not None:
            # Avoid duplicating identical bullets.
            prose_all = [audit_note_to_measurement_gap_prose(b) for b in bullets]
            prose_all = [b for b in prose_all if b]
            additions = [b for b in prose_all if b not in existing]
            if not additions:
                return text
            extra = "\n".join(f"- {b}" for b in additions)
            merged = (existing.rstrip() + "\n\n" + extra + "\n").lstrip("\n")
            return _replace_section(text, alias, merged)

    # Insert before Limitations / Source quality / References if present.
    insert_at = None
    for heading in ("## Limitations", "## Source quality", "## References"):
        idx = text.find(heading)
        if idx >= 0:
            insert_at = idx
            break
    chunk = f"{UNCERTAINTIES_HEADING}\n\n{block}\n\n"
    if insert_at is None:
        return text.rstrip() + "\n\n" + chunk
    return text[:insert_at] + chunk + text[insert_at:]


def _replace_section(text: str, name: str, new_body: str) -> str:
    pattern = re.compile(rf"(^##\s+{re.escape(name)}\s*$\n)", re.I | re.M)
    m = pattern.search(text)
    if not m:
        return text
    start = m.end()
    nxt = re.search(r"^##\s+", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    return text[:start] + "\n" + new_body.rstrip() + "\n\n" + text[end:]


def _exclude_violations(body: str, contract: ResearchContract) -> list[str]:
    """Find excluded-domain prose driving the memo (outside References)."""
    if not contract.excluded_domains or not body:
        return []
    # Scrub protected sections before scanning.
    scrubbed = body
    for protected in ("Source quality", "References"):
        sec = _section(scrubbed, protected)
        if sec is not None:
            scrubbed = scrubbed.replace(sec, "")

    hits: list[str] = []
    for dom in contract.excluded_domains:
        rx = EXCLUDE_HIT_RE.get(dom) or EXCLUDE_HIT_RE.get(dom.upper())
        if rx is None:
            rx = re.compile(rf"\b{re.escape(dom)}\b", re.I)
        if rx.search(scrubbed):
            hits.append(
                f"Excluded domain '{dom}' appears in memo prose; it must not drive "
                "Key findings / Detailed analysis for this contract."
            )
    return hits


def _scale_mismatch_gaps(body: str, contract: ResearchContract) -> list[str]:
    if not contract.scale_bounds or not body:
        return []
    memo_scales = extract_model_scales(body)
    asked = set(contract.scale_bounds)
    foreign = memo_scales - asked
    # Classic bleed: 1.5B figures next to asked 7B.
    if asked and foreign and is_memory_footprint_query(contract.query or body):
        if re.search(
            r"\b1\.5\s*[Bb]\b.{0,120}\b7\s*[Bb]\b|\b7\s*[Bb]\b.{0,120}\b1\.5\s*[Bb]\b",
            body,
            re.I | re.S,
        ):
            return [
                f"Scale mismatch risk: memo mixes {', '.join(sorted(foreign))} figures with "
                f"asked scale(s) {', '.join(sorted(asked))}. Keep each number scoped to the "
                "model size actually measured."
            ]
        # Softer note when foreign scales dominate memory claims.
        if re.search(r"\b(?:VRAM|peak\s+memory|GB)\b", body, re.I):
            return [
                f"Memo discusses model scales {', '.join(sorted(memo_scales))}; "
                f"contract asks for {', '.join(sorted(asked))}."
            ]
    return []


def _author_repo_identity_gaps(body: str, contract: ResearchContract) -> list[str]:
    """Heuristic: QLoRA/LoRA claims should acknowledge canonical authors/repos."""
    if not body:
        return []
    q = contract.query or ""
    if not (is_memory_footprint_query(q) or re.search(r"\b(?:LoRA|QLoRA)\b", q, re.I)):
        return []
    low = body.lower()
    gaps: list[str] = []
    for rule in AUTHOR_REPO_IDENTITY:
        if not rule["topic_re"].search(body):
            continue
        if rule["topic"] == "qlora" and not re.search(r"\bQLoRA\b", q + " " + body, re.I):
            continue
        has_author = any(a in low for a in rule["good_authors"])
        has_repo = any(r.lower() in low for r in rule["good_repos"])
        if rule["topic"] == "qlora" and not (has_author or has_repo):
            gaps.append(
                "QLoRA memory claims lack author/repo identity for Tim Dettmers / "
                "artidoro/qlora (or TimDettmers/bitsandbytes); prefer those primaries."
            )
        if rule["topic"] == "lora" and "qlora" not in low[:200] and not (has_author or has_repo):
            # Only flag when LoRA is central and Hu/microsoft missing AND QLoRA block
            # didn't already cover — keep soft.
            if re.search(r"\bLoRA\b", q, re.I) and "hu" not in low and "microsoft/lora" not in low:
                gaps.append(
                    "LoRA primary identity (Hu et al. / microsoft/LoRA) is not evident in the memo."
                )
        bad = rule.get("bad_misattr")
        if bad and bad.search(body):
            gaps.append(
                f"Possible mis-attribution of {rule['topic'].upper()} authorship; "
                "verify against Hu 2106.09685 / Dettmers 2305.14314."
            )
    return gaps


def audit_memo_against_contract(
    body: str,
    contract: ResearchContract | dict | None,
    *,
    query: str = "",
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> dict[str, Any]:
    """Audit draft memo vs contract; return gaps/flags and body with gaps section updated.

    Always invokes demote_off_scope_memo_content when a query is available (no duplicate
    demote logic here).
    """
    c = _as_contract(contract)
    text = body or ""
    flags: list[str] = []
    gaps: list[str] = []

    q = query or (c.query if c else "")
    demoted, demote_flags = demote_off_scope_memo_content(
        text,
        query=q,
        citations=citations,
        evidence=evidence,
    )
    if demote_flags:
        text = demoted
        flags.extend(demote_flags)

    if c is None:
        return {
            "body_markdown": text,
            "gaps": [],
            "flags": flags,
            "had_contract": False,
            "hard_fail": False,
            "should_block_publish": False,
            "missing_mandatory": [],
        }

    gaps.extend(_exclude_violations(text, c))
    gaps.extend(_scale_mismatch_gaps(text, c))
    gaps.extend(_author_repo_identity_gaps(text, c))

    missing = mandatory_sources_present(c, citations=citations, evidence=evidence)
    for src in missing:
        label = src.get("label") or src.get("arxiv_id") or "mandatory source"
        aid = src.get("arxiv_id") or ""
        gaps.append(
            f"Mandatory source missing from citations/evidence: {label}"
            + (f" ({aid})" if aid else "")
            + "."
        )
        flags.append(f"mandatory_missing_{aid or label}")

    # Hard fail: mandatory Hu/Dettmers (or other contract primaries) absent.
    # Keep Uncertainties bullets, but signal so memo_gate cannot soft-publish green.
    hard_fail = bool(missing)
    if hard_fail:
        flags.append("hard_fail_mandatory_missing")
        flags.append("should_block_publish")

    if gaps:
        flags.append("constraint_gaps_recorded")
        text = _replace_or_insert_uncertainties(text, gaps)

    return {
        "body_markdown": text,
        "gaps": gaps,
        "flags": flags,
        "had_contract": True,
        "missing_mandatory": missing,
        "hard_fail": hard_fail,
        "should_block_publish": hard_fail,
    }


def apply_constraint_audit(
    body: str,
    *,
    query: str = "",
    brief: dict | None = None,
    state: dict | None = None,
    contract: ResearchContract | dict | None = None,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> dict[str, Any]:
    """Fail-soft entry used by report._report_sync after integrity."""
    from app.domain.research_contract import contract_from_brief_or_state

    c = _as_contract(contract) or contract_from_brief_or_state(brief, state, query=query)
    if c is None:
        return {
            "body_markdown": body or "",
            "gaps": [],
            "flags": [],
            "had_contract": False,
            "skipped": True,
            "hard_fail": False,
            "should_block_publish": False,
            "missing_mandatory": [],
        }
    return audit_memo_against_contract(
        body,
        c,
        query=query or c.query,
        citations=citations,
        evidence=evidence,
    )
