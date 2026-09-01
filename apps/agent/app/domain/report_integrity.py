"""Post-write integrity pass: catch memo self-contradictions before publish."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.domain.report_audit import _section
from app.domain.research_intent import user_goal
from app.domain.schema import AgentName, SubQuery
from app.graph.serde import dump

UNRESOLVED_CITE_RE = re.compile(r"\[\?\]")
CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
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

    return notes


def enforce_report_integrity(
    *,
    body_markdown: str,
    executive_summary: str,
    decision_rule: str,
    at_a_glance: str,
    citations: list[dict],
    critic: dict,
    limitations: list[str],
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

    quant = _section(body, "Quantitative findings")
    quant_absent = _quantitative_data_absent(quant)

    if quant_absent:
        body = _label_illustrative_section(body, "Worked example")
        decision_rule = _sanitize_decision_rule_numbers(decision_rule, quant_absent=True)
        body = _rewrite_decision_rule_section(body, decision_rule)

    audit_notes = audit_memo_integrity(
        body,
        citations=citations,
        executive_summary=executive_summary,
        at_a_glance=at_a_glance,
    )
    limitations.extend(audit_notes)

    if not (at_a_glance or "").strip():
        at_a_glance = _derive_at_a_glance(executive_summary, decision_rule, critic)
    elif _normalize_prose(at_a_glance) and _normalize_prose(executive_summary):
        if SequenceMatcher(None, _normalize_prose(at_a_glance), _normalize_prose(executive_summary)).ratio() >= 0.82:
            at_a_glance = _derive_at_a_glance(executive_summary, decision_rule, critic)
            flags.append("regenerated_at_a_glance")

    depth = (critic.get("depth_score") or {}) if critic else {}
    breakdown = depth.get("breakdown") or {}
    confidence_breakdown = {
        "score": depth.get("score"),
        "label": depth.get("label"),
        "must_answer_pct": (depth.get("must_answer") or {}).get("pct"),
        "critical_pct": (depth.get("critical") or {}).get("pct"),
        "primary_sources_pct": (depth.get("primary_sources") or {}).get("pct"),
        "cross_validation_pct": (depth.get("cross_validation") or {}).get("pct"),
        "implementation_pct": (depth.get("implementation") or {}).get("pct"),
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


def _citation_stacks(text: str) -> list[str]:
    stacks: list[str] = []
    for line in (text or "").splitlines():
        cites = CITE_RE.findall(line)
        if not cites:
            continue
        total = sum(len(c.split(",")) for c in cites)
        if total >= 3:
            stacks.append(cites[0])
    return stacks


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


def _label_illustrative_section(body: str, heading: str) -> str:
    section = _section(body, heading)
    if not section or ILLUSTRATIVE_PREFIX.strip() in section:
        return body
    if not _uncited_load_bearing_numbers(section):
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
    new = ILLUSTRATIVE_PREFIX + old.lstrip()
    return body[:start] + new + body[end:]


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
    out = "\n".join(lines).strip()
    if in_empirical or "empirical cutoff" in (rule or "").lower():
        if not re.search(r"\d+(?:\.\d+)?\s*%", out) and "none" not in out.lower():
            out = (
                out.rstrip()
                + "\n\n**Evidence-backed threshold:** none in collected sources — "
                "use Engineering heuristics below for design guidance only."
            )
    return out


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
