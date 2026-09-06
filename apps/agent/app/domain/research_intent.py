from __future__ import annotations

import re
from urllib.parse import urlparse

from app.domain.decompose import derive_slots, must_cover_from_slots, subquestions_for
from app.domain.schema import SubQuery
from app.domain.textutil import (
    GOAL_META_RE,
    content_terms,
    distinctive_terms,
    entity_candidates,
    entity_pattern,
    user_goal,
)

__all__ = [
    "AUTHORITY_RANK",
    "GOAL_META_RE",
    "PRIMARY_CODE_HOSTS",
    "SECONDARY_HOSTS",
    "authority_score",
    "claim_confidence",
    "contradiction_signals",
    "coverage_gaps",
    "decision_rule_for",
    "decompose_subquestions",
    "demote_secondary",
    "host_of",
    "is_comparison_query",
    "is_mechanism_query",
    "is_secondary_host",
    "must_cover_for",
    "named_systems",
    "topic_leakage_reasons",
    "user_goal",
]

# A mechanism question asks what happens inside something, as opposed to what it
# affects. Both halves must be present so "how does X affect Y" stays a plain
# factual question.
MECHANISM_NOUN_RE = re.compile(
    r"\b(mechanism|internals|under the hood|architectur\w*|reference implementation|"
    r"pipeline|workflow|algorithm|kernel|data\s?flow|step[- ]by[- ]step|source code|"
    r"works? internally)\b",
    re.I,
)
HOW_WHY_RE = re.compile(r"\b(?:how|why)\b", re.I)
# "How does X affect Y", "how much", and "how should I" are not requests to
# reconstruct an internal process.
MECHANISM_EXCLUSION_RE = re.compile(
    r"\bhow\s+(?:much|many|long|often|far|fast|should|do i|can i|to)\b"
    r"|\bhow\b[^?.]{0,80}?\b(?:affect|impact|influence|improve|reduce|increase|matter|"
    r"compare[ds]?|differ|correlate)\b",
    re.I,
)
COMPARISON_RE = re.compile(
    r"\b(vs\.?|versus|compare[ds]?|comparison|difference[s]?|better than|instead of|"
    r"trade-?offs?|which (?:one|is better))\b",
    re.I,
)
DISAGREEMENT_RE = re.compile(
    r"\b(however|in contrast|contrary to|unlike|disagree|contradict|but in practice|"
    r"we find that .{0,40}not|fails? to|does not|cannot|no (?:significant )?(?:gain|benefit)|"
    r"overstated|misleading)\b",
    re.I,
)
SECONDARY_HOSTS = {
    "medium.com",
    "towardsai.net",
    "pub.towardsai.net",
    "towardsdatascience.com",
    "emergentmind.com",
    "learnaivisually.com",
    "substack.com",
    "dev.to",
    "hashnode.dev",
}
PRIMARY_CODE_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"}
PRIMARY_PAPER_HOSTS = {
    "arxiv.org",
    "export.arxiv.org",
    "doi.org",
    "aclanthology.org",
    "openreview.net",
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "pubmed.ncbi.nlm.nih.gov",
    "nature.com",
    "science.org",
}
AUTHORITY_RANK = {
    "official_regulation": 5,
    "intergovernmental": 4,
    "standard_body": 4,
    "peer_reviewed": 5,
    "specialist_research": 3,
    "industry_association": 2,
    "news_analysis": 1,
    "vendor_or_consultancy": 1,
    "unknown": 0,
}


def is_mechanism_query(query: str) -> bool:
    """True when the asker wants an explanation of how or why something works."""
    goal = user_goal(query)
    if MECHANISM_NOUN_RE.search(goal):
        return True
    return bool(HOW_WHY_RE.search(goal)) and not MECHANISM_EXCLUSION_RE.search(goal)


def is_comparison_query(query: str) -> bool:
    goal = user_goal(query)
    return bool(COMPARISON_RE.search(goal)) or len(entity_candidates(goal)) >= 2


def named_systems(query: str) -> list[str]:
    """Specific named subjects in the question, detected without any domain list."""
    return entity_candidates(user_goal(query), limit=8)


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def is_secondary_host(url_or_host: str) -> bool:
    host = host_of(url_or_host) if "://" in (url_or_host or "") else (url_or_host or "").lower()
    if not host:
        return False
    if host in SECONDARY_HOSTS:
        return True
    return any(host.endswith(f".{h}") for h in SECONDARY_HOSTS)


def authority_score(ev: dict) -> float:
    """Higher is better. Primary papers/repos beat blogs even if blogs are topical."""
    url = ev.get("url") or ""
    host = host_of(url)
    tier = (ev.get("tier") or "unknown").lower()
    score = float(AUTHORITY_RANK.get(tier, 0))
    score += float(ev.get("credibility") or 0) * 2.0
    if host in PRIMARY_PAPER_HOSTS:
        score += 2.5
    if host in PRIMARY_CODE_HOSTS:
        score += 2.0
    if "/docs" in (url or "") or "/documentation" in (url or ""):
        score += 1.0
    if is_secondary_host(host):
        score -= 3.5
    if ev.get("source_kind") == "corpus_note":
        score -= 1.0
    return score


def demote_secondary(evidence: list[dict]) -> list[dict]:
    """Keep secondary sources only as supplementary when primaries exist."""
    if not evidence:
        return []
    primaries = [e for e in evidence if not is_secondary_host(e.get("url") or "")]
    secondaries = [e for e in evidence if is_secondary_host(e.get("url") or "")]
    if not primaries:
        return evidence
    return primaries + secondaries[:1]


def claim_confidence(ev: dict, *, corroborating: int = 1) -> float:
    """Confidence from source authority and how specific the passage is."""
    tier = (ev.get("tier") or "").lower()
    quote = f"{ev.get('quote') or ''} {ev.get('snippet') or ''}"
    base = 0.42 + float(ev.get("credibility") or 0.4) * 0.35
    if tier in {"peer_reviewed", "official_regulation", "standard_body"}:
        base += 0.12
    elif tier == "specialist_research":
        base += 0.06
    elif tier in {"news_analysis", "vendor_or_consultancy", "unknown"}:
        base -= 0.08
    if is_secondary_host(ev.get("url") or ""):
        base -= 0.18
    base += min(0.06, 0.02 * _specificity_signals(quote))
    if corroborating >= 2:
        base += 0.04
    return round(min(0.92, max(0.35, base)), 3)


def _specificity_signals(text: str) -> int:
    """Domain-neutral markers that a passage states something concrete."""
    if not text:
        return 0
    signals = 0
    if re.search(r"\d", text):
        signals += 1
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|x\b|ms\b|gb\b|mb\b|tb\b|s\b|hours?|tokens?)", text, re.I):
        signals += 1
    if re.search(r"[A-Za-z_][A-Za-z0-9_]*\(\)|[a-z]+_[a-z]+|--[a-z-]+|\.[a-z]{2,4}\b", text):
        signals += 1
    if len(distinctive_terms(text, limit=6)) >= 4:
        signals += 1
    return signals


def must_cover_for(query: str) -> list[str]:
    """Coverage requirements derived from the question, not from a fixed template."""
    goal = user_goal(query)
    items = [f"Answer the question as asked: {goal}"] if goal else []
    return items + must_cover_from_slots(derive_slots(query))


def decompose_subquestions(query: str, remaining_calls: int = 8) -> list[SubQuery]:
    """Claim-seeking sub-queries for any question."""
    return subquestions_for(query, remaining_calls=remaining_calls)


def decision_rule_for(query: str, ledger: list, critic: dict) -> str:
    """An actionable rule built from what the evidence actually established."""
    goal = user_goal(query)
    coverage = (critic or {}).get("coverage") or {}
    slots = coverage.get("slots") or []
    supported = [s for s in slots if s.get("status") == "covered"]
    partial = [s for s in slots if s.get("status") == "weak"]
    missing = [s for s in slots if s.get("status") == "open"]
    n_src = len(ledger)

    lines: list[str] = [
        "### Empirical cutoffs (sources only)",
        "",
        "Only thresholds measured in a cited experiment belong here. "
        "If none were measured, write: *Evidence-backed threshold: none.*",
        "",
    ]
    empirical_bullets = False
    if supported:
        lines.append("**Act on these — the sources support them directly:**")
        lines.append("")
        for s in supported[:6]:
            label = s.get("label") or s.get("id")
            cite = s.get("support") or s.get("citation") or ""
            suffix = f" — see {cite}." if cite else f" — from the {n_src} cited sources."
            lines.append(f"- {label}{suffix}")
            empirical_bullets = True
        lines.append("")
    if not empirical_bullets:
        lines.append("*Evidence-backed threshold: none.*")
        lines.append("")

    lines.extend(
        [
            "### Engineering heuristics (AI suggestion — not from papers)",
            "",
            "Do **not** invent numeric cutoffs (tool-schema counts, % context filled, hop limits) "
            "unless a citation measured them. Prefer qualitative guidance only.",
            "",
        ]
    )
    if partial:
        lines.append("**Verify before acting — evidence is indirect or single-sourced:**")
        lines.append("")
        for s in partial[:6]:
            label = s.get("label") or s.get("id")
            lines.append(
                f"- Verify: {label} — weak/single-sourced; confirm with one independent primary source."
            )
        lines.append("")
    if missing:
        lines.append("**Do not assume — not established by cited sources:**")
        lines.append("")
        for s in missing[:6]:
            label = s.get("label") or s.get("id")
            lines.append(f"- Do not assume: {label}.")
        lines.append("")
    if not partial and not missing and not empirical_bullets:
        lines.append(
            f"For “{goal[:140]}”, act only on statements a cited primary source supports."
        )
        lines.append("")
    lines.append(
        f"Applied to “{goal[:140]}”: empirical bullets are decision input; verify/do-not-assume "
        f"are open. This rests on {n_src} cited sources."
    )
    return "\n".join(lines)


def field_unknowns_for(query: str, critic: dict | None = None) -> list[str]:
    """Genuine open measurement/field gaps — distinct from run Limitations."""
    goal = user_goal(query) or (query or "").strip()
    coverage = ((critic or {}).get("coverage") or {})
    open_slots = [
        s.get("label") or s.get("id")
        for s in (coverage.get("slots") or [])
        if s.get("status") == "open"
    ]
    unknowns = [
        f"No cited source measures “{goal[:100]}” under a single shared harness "
        "with reported N, success rate, and failure-mode breakdown.",
        "Cross-framework serving/runtime penalties (e.g. KV-cache invalidation under long tool "
        "trajectories) are rarely reported with comparable methodology across stacks.",
        "Replication: many mechanism claims rest on a single system paper without an independent "
        "reproduction study among the cited sources.",
    ]
    for label in open_slots[:3]:
        unknowns.append(f"Still open in the literature covered here: {label}.")
    return unknowns[:6]


def coverage_gaps(query: str, must_cover: list[str], evidence: list[dict]) -> list[str]:
    """Must-cover items the evidence blob barely touches."""
    blob = " ".join(
        f"{e.get('title', '')} {e.get('snippet', '')} {e.get('quote', '')}" for e in evidence
    ).lower()
    gaps: list[str] = []
    for item in must_cover or []:
        terms = content_terms(item, limit=8)
        if terms and sum(1 for t in terms if t in blob) < 2:
            gaps.append(f"Must-cover still thin: {item}")
    entities = named_systems(query)
    if len(entities) >= 2:
        missing = [e for e in entities if not re.search(entity_pattern(e), blob, re.I)]
        if missing:
            gaps.append(f"Named subject(s) under-covered: {', '.join(missing[:4])}.")
    return gaps[:6]


def _shares_no_distinctive_term(ev: dict, anchors: set[str]) -> bool:
    blob = f"{ev.get('title', '')} {ev.get('snippet', '')} {ev.get('quote', '')}".lower()
    return not any(term in blob for term in anchors)


def topic_leakage_reasons(query: str, evidence: list[dict]) -> list[str]:
    """Flag when the retrieved set drifted away from what was asked."""
    goal = user_goal(query)
    anchors = set(distinctive_terms(goal, limit=10))
    if not anchors or not evidence:
        return []
    off = sum(1 for ev in evidence if _shares_no_distinctive_term(ev, anchors))
    if off and off / len(evidence) >= 0.4:
        return [
            f"{off} of {len(evidence)} sources share no distinctive term with the question — "
            "the evidence set drifted off topic."
        ]
    return []


# Large enough to sink below any realistic authority_score + numeric_evidence_score
# combination (roughly -3.5..+16) so an off-topic source is essentially never the
# one chosen for extraction/citation, without being hard-dropped from the working
# set — critic can still fall back to it if the on-topic evidence runs out.
TOPIC_RELEVANCE_PENALTY = -10.0


def topic_relevance_penalty(ev: dict, query: str) -> float:
    """Rank penalty for a source sharing no distinctive term with the question.

    Per-source counterpart to topic_leakage_reasons, which only warns in
    aggregate (>=40% of the set) and never removes or demotes anything —
    that let sources like an unrelated soccer-workload or green-banking paper
    survive all the way into a synthetic-data-for-LLMs memo's References.
    """
    goal = user_goal(query)
    anchors = set(distinctive_terms(goal, limit=10))
    if not anchors:
        return 0.0
    return TOPIC_RELEVANCE_PENALTY if _shares_no_distinctive_term(ev, anchors) else 0.0


def contradiction_signals(query: str, evidence: list[dict]) -> list[str]:
    """Explicit disagreement in on-topic sources, without assuming a subject."""
    anchors = set(distinctive_terms(user_goal(query), limit=10))
    hits: list[str] = []
    for ev in evidence:
        blob = f"{ev.get('quote') or ''} {ev.get('snippet') or ''}"
        if not blob.strip():
            continue
        low = blob.lower()
        if anchors and not any(term in low for term in anchors):
            continue
        match = DISAGREEMENT_RE.search(blob)
        if match:
            title = (ev.get("title") or host_of(ev.get("url") or "") or "source")[:60]
            hits.append(f"{title} qualifies or disputes a central claim (“{match.group(0)}”).")
        if len(hits) >= 3:
            break
    return hits
