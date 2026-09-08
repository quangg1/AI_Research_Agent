from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.config.thresholds import CoverageThresholds
from app.domain.decompose import derive_slots, rewrite_gap_query
from app.domain.research_intent import (
    PRIMARY_CODE_HOSTS,
    PRIMARY_PAPER_HOSTS,
    contradiction_signals,
    host_of,
    is_secondary_host,
    user_goal,
)
from app.domain.schema import AgentName, SubQuery
from app.domain.textutil import distinctive_terms, entity_pattern

# Filler phrasing that carries no verifiable content, in any subject area.
GENERIC_CLAIM_RE = re.compile(
    r"(in recent years|has (?:recently )?(?:become|emerged|gained)|have (?:become|emerged|gained)|"
    r"(?:is|are) (?:widely|commonly|increasingly) (?:used|adopted|applied)|"
    r"(?:has|have) (?:attracted|drawn) (?:significant|considerable|much|growing) (?:attention|interest)|"
    r"plays? an? (?:important|crucial|key|vital) role|"
    r"rapidly (?:evolving|growing|developing|advancing)|"
    r"one of the most (?:popular|widely|important)|"
    r"(?:is|are) revolutioniz|"
    r"with the (?:rapid )?(?:development|advancement|rise) of)",
    re.I,
)

CODE_NOISE_RE = re.compile(r"/(issues|pull|discussions|releases|compare|commits)/", re.I)
CODE_SOURCE_RE = re.compile(
    r"/(?:blob|tree|raw)/|readme|\.(?:c|h|cc|cpp|cu|cuh|py|rs|go|java|ts|tsx|js|rb|swift|kt|scala)$",
    re.I,
)
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
REPO_RE = re.compile(r"(?:github|gitlab|bitbucket|codeberg)\.(?:com|org)/([^/]+)/([^/#?]+)", re.I)
OPENREVIEW_RE = re.compile(r"openreview\.net/(?:forum\?id=|pdf\?id=|attachment\?id=)([A-Za-z0-9_-]+)", re.I)
DOC_HOST_HINT_RE = re.compile(r"^(docs?|developer|developers|learn|api|help|support)\.", re.I)
TITLE_NOISE_RE = re.compile(
    r"\b(arxiv|preprint|under review|to appear|proceedings of|neurips|iclr|icml|acl|emnlp)\b",
    re.I,
)


def normalize_work_title(title: str) -> str:
    text = TITLE_NOISE_RE.sub(" ", (title or "").lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:100]


def work_identity(url: str, title: str = "") -> str:
    """Canonical work id so arXiv + OpenReview of the same paper count once."""
    raw = (url or "").strip()
    blob = f"{raw} {title or ''}"
    m = ARXIV_ID_RE.search(blob)
    if m:
        return f"arxiv:{m.group(1)}"
    om = OPENREVIEW_RE.search(raw)
    if om:
        return f"openreview:{om.group(1)}"
    nt = normalize_work_title(title)
    if len(nt) >= 28:
        return f"title:{nt}"
    return canonical_source_key(raw, title)


def _prefer_primary_venue(ev: dict) -> tuple[int, str]:
    url = (ev.get("url") or "").lower()
    if "arxiv.org" in url:
        return (0, url)
    if "openreview.net" in url:
        return (1, url)
    if "acm.org" in url or "ieee.org" in url or "neurips.cc" in url:
        return (0, url)
    return (2, url)


def canonical_source_key(url: str, title: str = "") -> str:
    raw = (url or "").strip()
    m = ARXIV_ID_RE.search(raw) or ARXIV_ID_RE.search(title or "")
    if m:
        return f"arxiv:{m.group(1)}"
    om = OPENREVIEW_RE.search(raw)
    if om:
        return f"openreview:{om.group(1)}"
    gm = REPO_RE.search(raw)
    if gm:
        return f"repo:{gm.group(1).lower()}/{gm.group(2).lower().removesuffix('.git')}"
    if raw.startswith("http"):
        try:
            p = urlparse(raw)
            path = (p.path or "").rstrip("/").lower()
            return f"{(p.hostname or '').lower().removeprefix('www.')}{path}"
        except Exception:
            return raw.rstrip("/").lower()
    return (title or raw or "").strip().lower()[:120]


def dedupe_evidence(evidence: list[dict]) -> list[dict]:
    """Drop venue duplicates (arXiv vs OpenReview) and identical URLs."""
    seen_ids: set[str] = set()
    title_to_id: dict[str, str] = {}
    out: list[dict] = []
    ordered = sorted(list(evidence or []), key=_prefer_primary_venue)
    for ev in ordered:
        url = ev.get("url") or ""
        title = ev.get("title") or ""
        wid = work_identity(url, title)
        nt = normalize_work_title(title)
        if wid in seen_ids:
            continue
        if nt and len(nt) >= 28 and nt in title_to_id:
            continue
        seen_ids.add(wid)
        if nt and len(nt) >= 28:
            title_to_id[nt] = wid
        row = dict(ev)
        row["canonical_key"] = wid
        out.append(row)
    return out


def _blob(ev: dict) -> str:
    return (
        f"{ev.get('title', '')} {ev.get('snippet', '')} {ev.get('quote', '')} "
        f"{ev.get('url', '')} {(ev.get('full_text') or '')[:1500]}"
    )


def _anchors(query: str) -> list[str]:
    """Distinctive terms for anchor scoring, using goal_with_named_subjects
    to recover entities from Constraints/Must cover lines."""
    from app.domain.textutil import goal_with_named_subjects
    return distinctive_terms(goal_with_named_subjects(query), limit=10)


def _anchor_hits(blob: str, anchors: list[str]) -> int:
    low = blob.lower()
    return sum(1 for term in anchors if term in low)


def is_official_implementation(ev: dict, query: str = "") -> bool:
    """Source-level code evidence, not a bug report or a random mirror."""
    url = ev.get("url") or ""
    if host_of(url) not in PRIMARY_CODE_HOSTS:
        return False
    if CODE_NOISE_RE.search(url):
        return False
    repo = REPO_RE.search(url)
    if not repo:
        return False
    path = urlparse(url).path or ""
    owner_repo = f"{repo.group(1)} {repo.group(2)}".lower()
    anchors = _anchors(query) if query else []
    if anchors:
        blob = f"{owner_repo} {ev.get('title', '')} {ev.get('snippet', '')}"
        if _anchor_hits(blob, anchors) == 0:
            return False
    depth = len([p for p in path.split("/") if p])
    return depth <= 2 or bool(CODE_SOURCE_RE.search(path))


def assign_source_role(ev: dict, query: str = "") -> str:
    url = ev.get("url") or ""
    host = host_of(url)
    blob = _blob(ev)
    anchors = _anchors(query) if query else []
    if anchors and _anchor_hits(blob, anchors) == 0:
        return "off_topic"
    if host in PRIMARY_CODE_HOSTS:
        if CODE_NOISE_RE.search(url):
            return "code_discussion"
        return "implementation" if is_official_implementation(ev, query) else "third_party_code"
    if host in PRIMARY_PAPER_HOSTS:
        return "primary_paper"
    if is_secondary_host(url):
        return "secondary_article"
    if DOC_HOST_HINT_RE.match(host) or "/docs" in url or "/documentation" in url:
        return "official_docs"
    return "reference"


def tag_evidence_roles(evidence: list[dict], query: str = "") -> list[dict]:
    anchors = _anchors(query) if query else []
    out: list[dict] = []
    for ev in dedupe_evidence(evidence):
        row = dict(ev)
        role = assign_source_role(ev, query)
        row["source_role"] = role
        row["is_official_impl"] = is_official_implementation(ev, query)
        if role == "off_topic":
            row["off_topic"] = True
            row["credibility"] = min(float(row.get("credibility") or 0.3), 0.25)
        elif role == "code_discussion":
            row["off_topic"] = True
            row["credibility"] = min(float(row.get("credibility") or 0.3), 0.22)
        if anchors:
            row["anchor_hits"] = _anchor_hits(_blob(ev), anchors)
        out.append(row)
    return out


def evidence_type_of(ev: dict, query: str = "") -> str:
    role = ev.get("source_role") or assign_source_role(ev, query)
    if ev.get("is_official_impl") or role == "implementation":
        return "official_repo"
    if role == "primary_paper":
        return "primary_paper"
    if role == "official_docs":
        return "official_docs"
    if role == "third_party_code":
        return "third_party_implementation"
    if role in {"secondary_article"}:
        return "secondary_article"
    if role in {"code_discussion", "off_topic"}:
        return "low_signal"
    return "reference"


def must_answer_for(query: str) -> list[dict[str, Any]]:
    """Must-answer dimensions for any question, derived from the question itself."""
    return derive_slots(query)


def slot_label(slot_id: str, slots: list[dict] | None = None) -> str:
    for slot in slots or []:
        if slot.get("id") == slot_id:
            return str(slot.get("label") or slot_id)
    return (slot_id or "").replace("_", " ").strip().capitalize() or "Dimension"


def score_must_answer(query: str, evidence: list[dict], slots: list[dict] | None = None) -> dict[str, Any]:
    slots = [dict(s) for s in (slots or must_answer_for(query))]
    anchors = _anchors(query)
    tagged = tag_evidence_roles(evidence, query)
    usable = [e for e in tagged if not e.get("off_topic")] or tagged

    for slot in slots:
        patterns = [p for p in (slot.get("patterns") or []) if p]
        topic_terms = [t for t in (slot.get("topic_terms") or anchors) if t]
        hits: list[dict] = []
        for ev in usable:
            blob = _blob(ev)
            aspect_hits = sum(1 for p in patterns if re.search(p, blob, re.I))
            topic_hits = _anchor_hits(blob, topic_terms)
            if aspect_hits <= 0 and topic_hits < 2:
                continue
            hits.append(
                {
                    **ev,
                    "_aspect": aspect_hits,
                    "_topic": topic_hits,
                    "_score": aspect_hits * 2 + min(topic_hits, 3),
                }
            )
        hits.sort(key=lambda e: (e.get("_score", 0), float(e.get("credibility") or 0)), reverse=True)
        if not hits:
            slot["status"] = "open"
            slot["evidence_ids"] = []
            slot["evidence_type"] = ""
            continue
        best = hits[0]
        ev_type = evidence_type_of(best, query)
        is_primary = ev_type in {"primary_paper", "official_repo", "official_docs"}
        
        # Count distinct works for this slot to prevent monoculture
        distinct_works = len({work_identity(h.get("url", ""), h.get("title", "")) for h in hits})
        
        strong = (
            best.get("_aspect", 0) >= 1
            and (
                best.get("_topic", 0) >= 2
                or (is_primary and best.get("_topic", 0) >= 1)
            )
            and not is_secondary_host(best.get("url") or "")
            and distinct_works >= 2  # Require ≥2 distinct works for covered status
        )
        # One work → at most weak, prevents SWE-Bench monoculture
        slot["status"] = "covered" if strong else "weak"
        slot["evidence_ids"] = [h.get("id") for h in hits[:3] if h.get("id")]
        slot["evidence_type"] = evidence_type_of(best, query)

    covered = sum(1 for s in slots if s["status"] == "covered")
    weak = sum(1 for s in slots if s["status"] == "weak")
    open_n = sum(1 for s in slots if s["status"] == "open")
    total = max(1, len(slots))
    critical_slots = [s for s in slots if s.get("critical")]
    crit_covered = sum(1 for s in critical_slots if s["status"] == "covered")
    crit_weak = sum(1 for s in critical_slots if s["status"] == "weak")
    crit_total = max(1, len(critical_slots))
    critical_gaps = [s for s in critical_slots if s["status"] != "covered"]

    official_impls = [e for e in tagged if e.get("is_official_impl")]
    primary_n = sum(
        1
        for e in tagged
        if evidence_type_of(e, query) in {"primary_paper", "official_repo", "official_docs"}
        and not e.get("off_topic")
    )
    # Calculate work diversity (distinct canonical works, not just hosts)
    work_ids = [work_identity(e.get("url", ""), e.get("title", "")) for e in tagged if e.get("url")]
    unique_works = len(set(work_ids))
    # Also track work concentration for monoculture detection
    from collections import Counter
    work_counter = Counter(work_ids) if work_ids else Counter()
    top_work_share = max(work_counter.values()) / max(1, len(work_ids)) if work_ids else 0.0
    
    quality = _research_quality(
        slots=slots,
        covered=covered,
        weak=weak,
        open_n=open_n,
        total=total,
        crit_covered=crit_covered,
        crit_weak=crit_weak,
        crit_total=crit_total,
        official_impls=len(official_impls),
        primary_n=primary_n,
        unique_n=len(tagged),
        unique_works=unique_works,
        top_work_share=top_work_share,
        critical_gaps=critical_gaps,
        query=query,
        evidence=usable,
    )

    return {
        "slots": slots,
        "covered": covered,
        "weak": weak,
        "open": open_n,
        "total": total,
        "ratio": round(covered / total, 3),
        "must_answer_fraction": f"{covered}/{total}",
        "critical_fraction": f"{crit_covered}/{crit_total}",
        "critical_ratio": round(crit_covered / crit_total, 3),
        "critical_gaps": [
            {"id": s["id"], "label": s["label"], "followup": s.get("followup", ""), "status": s.get("status")}
            for s in critical_gaps
        ],
        "weak_slots": [{"id": s["id"], "label": s["label"]} for s in slots if s["status"] == "weak"],
        "strong_slots": [{"id": s["id"], "label": s["label"]} for s in slots if s["status"] == "covered"],
        "has_implementation": bool(official_impls),
        "official_impl_count": len(official_impls),
        "unique_sources": len(tagged),
        "unique_hosts": len({h for h in hosts if h}),
        "primary_sources": primary_n,
        "depth_score": quality,
        "roles_present": sorted({e.get("source_role") for e in tagged if e.get("source_role")}),
        "contradictions": contradiction_signals(query, usable),
    }


def _wants_implementation(slots: list[dict]) -> bool:
    return any(
        re.search(r"implement|source|repo|code|library|api", f"{s.get('id')} {s.get('label')}", re.I)
        for s in slots
    )


def _research_quality(
    *,
    slots: list[dict],
    covered: int,
    weak: int,
    open_n: int,
    total: int,
    crit_covered: int,
    crit_weak: int,
    crit_total: int,
    official_impls: int,
    primary_n: int,
    unique_n: int,
    unique_works: int,
    top_work_share: float,
    critical_gaps: list,
    query: str = "",
    evidence: list[dict] | None = None,
) -> dict[str, Any]:
    must_pct = int(round(100 * covered / max(1, total)))
    crit_effective = crit_covered + 0.5 * crit_weak
    crit_pct = int(round(100 * crit_effective / max(1, crit_total)))
    primary_pct = int(round(100 * min(1.0, primary_n / max(3, total // 2))))
    cross_pct = int(round(100 * min(1.0, covered / 4)))
    # Changed from unique_hosts to unique_works (canonical work identities)
    # This prevents 8 arXiv papers (1 host) from scoring as diverse
    diversity_pct = int(round(100 * min(1.0, unique_works / 4)))

    # Does this question actually need a numbers table? Skip the penalty for
    # architecture/why-questions where a thin Quantitative findings section
    # is expected, not a red flag.
    from app.domain.adversarial import extract_quantitative_rows, numeric_rank_weight

    wants_numbers = numeric_rank_weight(query) >= 1.0
    quant_rows = len(extract_quantitative_rows(evidence or [])) if wants_numbers else 0
    # 3 grounded rows = full credit, mirrors the primary_n<3 sparsity check below.
    quant_pct = 100 if not wants_numbers else int(round(100 * min(1.0, quant_rows / 3)))

    wants_impl = _wants_implementation(slots)
    impl_slots = [
        s for s in slots if re.search(r"implement|source|repo|code", f"{s.get('id')} {s.get('label')}", re.I)
    ]
    if wants_impl:
        if impl_slots and all(s.get("status") == "covered" for s in impl_slots):
            impl_pct = 100
        elif official_impls or any(s.get("status") == "weak" for s in impl_slots):
            impl_pct = 50
        else:
            impl_pct = 0
        fifth_label, fifth_pct = "implementation_evidence", impl_pct
    else:
        fifth_label, fifth_pct = "source_diversity", diversity_pct

    overall = int(
        round(
            0.35 * must_pct
            + 0.30 * crit_pct
            + 0.15 * primary_pct
            + 0.05 * cross_pct
            + 0.05 * fifth_pct
            + 0.10 * quant_pct
        )
    )

    # Apply penalties for known limitations
    if any(s.get("status") == "weak" for s in slots if s.get("critical")):
        overall = min(overall, 80)
    if any(s.get("status") == "open" for s in slots if s.get("critical")):
        overall = min(overall, 65)
    
    # Issue #6 fix: Adjust floor based on evidence quantity
    # Old: Always capped at 55 when primary_n == 0
    # New: Lower floor when evidence is also extremely sparse
    total_evidence = len(evidence or [])
    if primary_n == 0:
        if total_evidence < 3:
            overall = min(overall, 35)  # Extremely sparse + no primary = very low confidence
        elif total_evidence < 5:
            overall = min(overall, 45)  # Very sparse + no primary = low confidence
        else:
            overall = min(overall, 55)  # Original floor
    
    # A numeric-heavy question with a near-empty measured table should not
    # read as fully confident even when coverage/primary-source checks pass.
    if wants_numbers and quant_rows < 2:
        overall = min(overall, 70)
    
    # ADDITIONAL: Reduce confidence when gaps/unknowns are present
    # These penalties prevent 100/100 when report lists uncertainties
    gaps_penalty = 0
    
    # Penalty for open gaps (even non-critical ones indicate incomplete coverage)
    if open_n > 0:
        gaps_penalty += min(10, open_n * 3)  # 3 points per open gap, max 10
    
    # Penalty for weak evidence (indicates uncertainty)
    if weak > 0:
        gaps_penalty += min(5, weak * 2)  # 2 points per weak slot, max 5
    
    # Penalty for low primary source count (measurement gap indicator)
    if primary_n > 0 and primary_n < 3:
        gaps_penalty += 5  # Sparse primary sources = likely measurement gaps
    
    # Issue #6 fix: Penalty for very sparse total evidence
    # When evidence is extremely thin, confidence should clearly reflect this
    total_evidence = len(evidence or [])
    if total_evidence > 0:
        if total_evidence < 3:
            # Extremely sparse: < 3 items total
            gaps_penalty += 25  # Major penalty - confidence should be very low
        elif total_evidence < 5:
            # Very sparse: 3-4 items
            gaps_penalty += 15  # Significant penalty
        elif total_evidence < 8:
            # Sparse: 5-7 items
            gaps_penalty += 8  # Moderate penalty
    
    # Apply penalties (no floor - allow score to drop to shallow if warranted)
    if gaps_penalty > 0:
        overall = max(overall - gaps_penalty, 0)
    
    # Coverage cap: must-answer < 50% should never read as "standard"
    # Prevents 33% coverage from showing 62/100 · standard
    # Issue #6 enhancement: More aggressive cap for very low coverage
    if must_pct < 50:
        overall = min(overall, 45)  # Force into "shallow" band
    elif must_pct < 60:
        overall = min(overall, 52)  # Still shallow, but closer to threshold
    
    # Work-concentration cap: one work dominating citations is a monoculture
    # Example: 8 papers, 6 from arxiv:2512.17419 → top_work_share = 0.75
    # Cap prevents SWE-Bench monoculture from scoring as diverse/confident
    if top_work_share > 0.35:
        overall = min(overall, 70)

    label = "deep" if overall >= 85 and not critical_gaps else "standard" if overall >= 55 else "shallow"
    if critical_gaps:
        label = "standard" if overall >= 55 else "shallow"

    return {
        "score": overall,
        "label": label,
        "must_answer": {"covered": covered, "total": total, "pct": must_pct, "fraction": f"{covered}/{total}"},
        "critical": {
            "covered": crit_covered,
            "total": crit_total,
            "pct": crit_pct,
            "fraction": f"{crit_covered}/{crit_total}",
        },
        "primary_support_pct": primary_pct,
        "implementation_pct": fifth_pct if fifth_label == "implementation_evidence" else None,
        "source_diversity_pct": diversity_pct,
        "cross_validation_pct": cross_pct,
        "quantitative_evidence_pct": quant_pct if wants_numbers else None,
        # Nested {pct: ...} shape — report_integrity.confidence_breakdown reads these,
        # not the flat _pct siblings above (kept for other/older callers).
        "primary_sources": {"pct": primary_pct},
        "cross_validation": {"pct": cross_pct},
        "implementation": {"pct": fifth_pct} if fifth_label == "implementation_evidence" else {},
        "quantitative_evidence": {"pct": quant_pct} if wants_numbers else {},
        "unique_sources": unique_n,
        "breakdown": {
            "must_answer_coverage": must_pct,
            "critical_coverage": crit_pct,
            "primary_source_support": primary_pct,
            "cross_source_validation": cross_pct,
            fifth_label: fifth_pct,
            **({"quantitative_evidence_support": quant_pct} if wants_numbers else {}),
        },
    }


def critic_should_pass(query: str, coverage: dict[str, Any], evidence: list[dict]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not evidence:
        return False, ["No evidence collected."]
    
    # Entity gate: each named subject must have dedicated evidence
    expected_entities = named_systems(query)
    if expected_entities:
        found_entities = entities_with_evidence(query, evidence, limit=99)
        missing = [e for e in expected_entities if e not in found_entities]
        if missing:
            reasons.append(f"Named subjects with no dedicated evidence: {', '.join(missing)}")
    
    for gap in coverage.get("critical_gaps") or []:
        status = gap.get("status") or "open"
        reasons.append(f"Critical dimension {status}: {gap.get('label') or gap.get('id')}")
    if not coverage.get("primary_sources"):
        reasons.append("No primary paper, official doc, or source repository in the working set.")
    must_pct = (coverage.get("depth_score") or {}).get("must_answer", {}).get("pct")
    if must_pct is None:
        must_pct = int(round(100 * float(coverage.get("ratio") or 0)))
    # FIXED: Align with memo_quality.py threshold to prevent dead zone
    # where critic passes but memo_quality refuses regeneration
    if must_pct < CoverageThresholds.MUST_COVERAGE_GOOD:
        reasons.append(f"Must-answer coverage {must_pct}% (<{CoverageThresholds.MUST_COVERAGE_GOOD}%).")
    slots = coverage.get("slots") or []
    if _wants_implementation(slots) and not coverage.get("has_implementation"):
        impl_open = [
            s for s in slots
            if re.search(r"implement|source|repo|code", f"{s.get('id')} {s.get('label')}", re.I)
            and s.get("status") != "covered"
        ]
        if impl_open:
            reasons.append("The question asks about implementation but no source-level evidence was found.")
    return (len(reasons) == 0), reasons


def followups_for_gaps(
    query: str,
    coverage: dict[str, Any],
    limit: int = 2,
    *,
    use_llm: bool = False,
) -> list[SubQuery]:
    """Generate targeted followup queries for coverage gaps.
    
    Enhanced to create specific queries using:
    - Dimension patterns for context
    - Entity names for targeted search
    - Domain-specific routing (arxiv/github/benchmark)
    """
    ordered: list[dict] = list(coverage.get("critical_gaps") or [])
    known = {g.get("id") for g in ordered}
    for slot in coverage.get("slots") or []:
        if slot.get("status") in {"open", "weak"} and slot.get("id") not in known:
            ordered.append(slot)
            known.add(slot.get("id"))
    
    out: list[SubQuery] = []
    
    # Extract entities for targeted queries
    from app.domain.textutil import entity_candidates
    entities = entity_candidates(user_goal(query), limit=8)
    
    for gap in ordered[:limit]:
        gap_id = str(gap.get("id") or "").lower()
        gap_label = gap.get("label") or ""
        patterns = [p for p in (gap.get("patterns") or []) if p]
        
        # Enhance gap with patterns for more specific queries
        gap_with_patterns = dict(gap)
        if patterns and not gap.get("followup"):
            # Add top patterns to gap text for rewrite_gap_query
            pattern_text = " ".join(patterns[:3])
            gap_with_patterns["followup"] = f"{gap_label}: {pattern_text}"
        
        # Route to appropriate agent based on gap type
        if "implement" in gap_id or "code" in gap_id or "source" in gap_id:
            # Implementation gap: prefer search for GitHub/code
            gap_with_patterns["agent_hint"] = "search_implementation"
        elif "benchmark" in gap_id or "evaluat" in gap_id or "metric" in gap_id:
            # Evaluation gap: prefer scholar for academic papers
            gap_with_patterns["agent_hint"] = "scholar_benchmark"
        elif "theor" in gap_id or "concept" in gap_id or "mechanism" in gap_id:
            # Theory gap: strongly prefer scholar
            gap_with_patterns["agent_hint"] = "scholar_theory"
        
        question, agent = rewrite_gap_query(query, gap_with_patterns, use_llm=use_llm)
        
        # Override agent based on hint if provided
        hint = gap_with_patterns.get("agent_hint", "")
        if hint.startswith("scholar"):
            agent = AgentName.SCHOLAR
        elif hint.startswith("search") and "implementation" in hint:
            agent = AgentName.SEARCH
        
        # Enhance question with arxiv/github prefix for better targeting
        if agent == AgentName.SCHOLAR and entities:
            # Add arxiv hint for theory/concept queries
            if not question.lower().startswith("arxiv"):
                entity_str = " ".join(entities[:2])
                question = f"arxiv papers: {entity_str} {question}".strip()[:200]
        elif agent == AgentName.SEARCH and "implementation" in hint:
            # Add github hint for implementation queries
            if not question.lower().startswith("github"):
                entity_str = " ".join(entities[:2])
                question = f"github source: {entity_str}".strip()[:200]
        
        out.append(
            SubQuery(
                agent=agent,
                question=question,
                rationale=f"Fill must-answer gap: {gap_label}",
            )
        )
    
    return out


def claims_from_must_answer(coverage: dict[str, Any], evidence: list[dict]) -> list[dict]:
    """Claims are the question's own dimensions, with evidence attached when found."""
    from app.domain.research_intent import claim_confidence

    by_id = {e.get("id"): e for e in evidence}
    claims: list[dict] = []
    n = 1
    for slot in coverage.get("slots") or []:
        if slot.get("status") == "open" and not slot.get("critical"):
            continue
        eid = (slot.get("evidence_ids") or [None])[0]
        ev = by_id.get(eid) or {}
        raw_quote = (ev.get("quote") or ev.get("snippet") or "").strip()
        quote = "" if (not raw_quote or GENERIC_CLAIM_RE.search(raw_quote)) else raw_quote[:320]
        text = slot.get("label") or ""
        if slot.get("status") == "open":
            text = f"{text} — UNVERIFIED (no evidence yet)"
            quote = ""
        conf = 0.35 if slot.get("status") == "open" else claim_confidence(
            ev or {"tier": "unknown", "credibility": 0.4, "quote": quote, "url": ev.get("url")},
            corroborating=len(slot.get("evidence_ids") or []),
        )
        if slot.get("status") == "weak":
            conf = min(conf, 0.58)
        if slot.get("status") == "covered":
            conf = min(0.90, max(conf, 0.72))
        directness = (
            "direct"
            if slot.get("status") == "covered"
            else ("indirect" if slot.get("status") == "weak" else "unverified")
        )
        claims.append(
            {
                "id": f"C{n}",
                "text": text[:400],
                "quote": quote,
                "url": ev.get("url") or "",
                "tier": ev.get("tier") or "",
                "support_ids": [eid] if eid else [],
                "contradict_ids": [],
                "confidence": conf,
                "caveats": (
                    ["Weak — needs a stronger primary source"]
                    if slot.get("status") == "weak"
                    else (["Open gap — follow-up research required"] if slot.get("status") == "open" else [])
                ),
                "grounded": bool(eid) and slot.get("status") != "open",
                "slot_id": slot.get("id"),
                "priority": slot.get("priority") or ("critical" if slot.get("critical") else "standard"),
                "status": slot.get("status"),
                "evidence_type": slot.get("evidence_type") or (evidence_type_of(ev) if ev else "unverified"),
                "directness": directness,
            }
        )
        n += 1
    return claims


def build_evidence_dossier(
    query: str,
    evidence: list[dict],
    coverage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evidence grouped by must-answer dimension, ordered as the question implies.
    
    Each dimension gets evidence reranked by dimension-specific relevance,
    not just the global top-k pool.
    """
    from app.retrieval.passage import select_best_excerpts_per_dimension
    
    slots = (coverage or {}).get("slots") or must_answer_for(query)
    by_id = {e.get("id"): e for e in evidence if e.get("id")}
    anchors = _anchors(query)
    dossier: list[dict[str, Any]] = []
    for slot in slots:
        items: list[dict] = []
        seen: set[str] = set()
        
        # First, get pre-assigned evidence IDs from coverage
        for eid in slot.get("evidence_ids") or []:
            ev = by_id.get(eid)
            if ev and eid not in seen:
                items.append(ev)
                seen.add(eid)
        
        # If we don't have enough, rerank entire pool by dimension-specific patterns
        if len(items) < 3:
            patterns = [p for p in (slot.get("patterns") or []) if p]
            topic_terms = [t for t in (slot.get("topic_terms") or anchors) if t]
            dim_label = slot.get("label") or ""
            
            # Build dimension query for embedding similarity
            dim_query = f"{dim_label}. {'. '.join(patterns[:3])}" if patterns else dim_label
            
            # Score all evidence by relevance to THIS dimension
            dim_scored: list[tuple[float, dict]] = []
            for ev in evidence:
                eid = ev.get("id") or ""
                if eid in seen:
                    continue
                blob = _blob(ev)
                
                # Dimension-specific scoring: patterns + topic terms + embeddings
                aspect_hits = sum(1 for p in patterns if re.search(p, blob, re.I))
                topic_hits = _anchor_hits(blob, topic_terms)
                
                # Add embedding similarity if available (0-1 scale)
                embedding_score = 0.0
                if dim_query and blob:
                    try:
                        from app.retrieval.embed import semantic_similarity
                        embedding_score = semantic_similarity(dim_query, blob)
                    except Exception:
                        pass
                
                # Require at least some relevance to this dimension
                # Lower threshold if we have strong embedding similarity
                if aspect_hits == 0 and topic_hits < 2 and embedding_score < 0.4:
                    continue
                
                # Combined score: pattern hits (weighted high) + topic hits + embedding similarity
                # Embedding similarity on 0-1 scale, so multiply by 3 to make it comparable to aspect hits
                score = aspect_hits * 3 + topic_hits + (embedding_score * 3)
                dim_scored.append((score, ev))
            
            # Take top items for this dimension
            dim_scored.sort(key=lambda x: (x[0], float(x[1].get("credibility") or 0)), reverse=True)
            for _, ev in dim_scored[:3 - len(items)]:
                eid = ev.get("id") or ""
                if eid and eid not in seen:
                    items.append(ev)
                    seen.add(eid)
        
        dossier.append(
            {
                "id": slot.get("id"),
                "label": slot.get("label") or slot_label(slot.get("id") or ""),
                "critical": bool(slot.get("critical")),
                "status": slot.get("status") or ("covered" if items else "open"),
                "items": items[:3],  # Max 3 per dimension
            }
        )
    
    # Now apply passage-level selection within each dimension's evidence
    dossier_with_passages = select_best_excerpts_per_dimension(dossier, evidence)
    return dossier_with_passages


def entities_with_evidence(query: str, evidence: list[dict], limit: int = 4) -> list[str]:
    """Named subjects from the question that the evidence actually discusses.

    Returns named entities that have dedicated evidence. Does NOT filter out
    entities appearing in most sources - that filter was correct for generic
    topics but wrong for entity gates (a comparison query legitimately needs
    every named subject to have evidence).
    """
    from app.domain.research_intent import named_systems

    candidates = named_systems(query)
    if not candidates or not evidence:
        return []
    n = len(evidence)
    scored: list[tuple[int, str]] = []
    for name in candidates:
        pattern = entity_pattern(name)
        df = sum(
            1
            for ev in evidence
            if re.search(pattern, f"{ev.get('title', '')} {ev.get('snippet', '')} {ev.get('quote', '')}", re.I)
        )
        if df == 0:
            continue
        # OLD LOGIC (removed): filtered entities appearing in most sources
        # This was correct for generic topics but WRONG for entity gates
        # A comparison query legitimately needs all named subjects
        scored.append((df, name))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in scored[:limit]]
