"""Evidence pipeline diagnostics and cheap pre-fetch scoring.

Distinguishes three independent failure modes:
  A) evidence_degradation — fetched content lost in state merge
  B) source_inaccessible — identity known but no deep representation
  C) budget_exhausted — orchestration stopped before gaps closed
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.adversarial import BENCHMARK_RE, MEASURED_RE, QUANT_RE, extract_quantitative_rows, numeric_evidence_score

_QUANT_SIGNAL_MARKERS = re.compile(
    r"\b("
    r"accuracy|f1|auc|bleu|rouge|latency|throughput|speedup|improvement|"
    r"ablation|benchmark|baseline|confidence interval|±|p\s*[<=>]\s*0\.\d+|"
    r"pass@\d+|win rate|error rate|precision|recall"
    r")\b",
    re.I,
)

_IDENTITY_ONLY_HOSTS = ("doi.org",)
_ABSTRACT_LIKELY_HOSTS = ("pubmed.ncbi.nlm.nih.gov", "neurips.cc", "openreview.net")
_PDF_LIKELY_HOSTS = ("arxiv.org", "aclanthology.org", "semanticscholar.org")


def quant_signal_score(ev: dict) -> float:
    """Cheap detector: does this excerpt likely contain extractable quantitative evidence?"""
    search = str(ev.get("search_snippet") or "")
    snippet = str(ev.get("snippet") or "")
    full = str(ev.get("full_text") or "")
    # Prefer search-stage text for pre-fetch scoring; fall back to any excerpt.
    blob = search or snippet or full[:2000]
    if not blob.strip():
        return 0.0
    score = numeric_evidence_score({**ev, "snippet": blob, "full_text": ""})
    if _QUANT_SIGNAL_MARKERS.search(blob):
        score += 1.25
    if QUANT_RE.search(blob):
        score += 0.75
    if BENCHMARK_RE.search(blob):
        score += 1.0
    if MEASURED_RE.search(blob):
        score += 0.5
    return round(score, 2)


def quant_signal_level(ev: dict) -> str:
    score = quant_signal_score(ev)
    if score >= 2.0:
        return "high"
    if score >= 0.8:
        return "medium"
    return "low"


def access_tier(url: str) -> str:
    """How likely is a deep fetch to yield extractable body text?"""
    low = (url or "").lower()
    if any(h in low for h in _IDENTITY_ONLY_HOSTS):
        return "identity_only"
    if low.endswith(".pdf") or "/pdf/" in low or "arxiv.org/pdf/" in low:
        return "pdf_likely"
    if any(h in low for h in _PDF_LIKELY_HOSTS):
        return "html_or_pdf"
    if any(h in low for h in _ABSTRACT_LIKELY_HOSTS):
        return "abstract_likely"
    if "github.com" in low or "gitlab.com" in low:
        return "repo_readme"
    return "html_unknown"


_ACCESS_SCORE = {
    "pdf_likely": 1.0,
    "html_or_pdf": 0.85,
    "html_unknown": 0.55,
    "repo_readme": 0.45,
    "abstract_likely": 0.25,
    "identity_only": 0.1,
}


def access_score(url: str) -> float:
    return _ACCESS_SCORE.get(access_tier(url), 0.4)


def access_allows_fetch(url: str, ev: dict | None = None) -> bool:
    """Hard gate: skip identity-only and low-signal abstract-only pages."""
    tier = access_tier(url)
    if tier == "identity_only":
        return False
    if tier == "abstract_likely" and quant_signal_score(ev or {"url": url}) < 0.8:
        return False
    return True


def expected_fetch_priority(ev: dict, url: str = "") -> float:
    """Higher = fetch first. Access is a gate; relevance + quant signal drive rank."""
    u = url or str(ev.get("url") or "")
    if not access_allows_fetch(u, ev):
        return -1.0
    low = u.lower()
    host_tier = 0.0
    if "arxiv.org" in low or "aclanthology.org" in low:
        host_tier = 4.0
    elif "openreview.net" in low:
        host_tier = 3.0
    elif "github.com" in low:
        host_tier = 2.0
    elif any(m in low for m in ("docs.", ".gov", "openai.com", "anthropic.com")):
        host_tier = 1.5
    q = quant_signal_score(ev)
    thin = 0.0 if len(str(ev.get("full_text") or "")) >= 1600 else 1.0
    return host_tier * 2.0 + q * 4.0 + thin


def _is_fetched(ev: dict) -> bool:
    ft = str(ev.get("full_text") or "")
    return len(ft) >= 40 or ev.get("fetch_status") == "ok"


def _is_accessible(ev: dict) -> bool:
    blob = str(ev.get("full_text") or ev.get("snippet") or ev.get("search_snippet") or "")
    return len(blob.strip()) >= 200 and not looks_like_failed_fetch(ev)


def looks_like_failed_fetch(ev: dict) -> bool:
    blob = (str(ev.get("full_text") or ev.get("snippet") or ""))[:500].lower()
    if len(blob) < 80:
        return True
    fail_markers = (
        "access denied",
        "403 forbidden",
        "404 not found",
        "enable javascript",
        "captcha",
        "sign in to continue",
    )
    return any(m in blob for m in fail_markers)


def classify_source_representation(ev: dict) -> str:
    if looks_like_failed_fetch(ev):
        return "fetch_failed"
    if _is_fetched(ev):
        return "fetched_full"
    if quant_signal_level(ev) == "high" and not _is_fetched(ev):
        return "thin_excerpt_high_signal"
    if access_tier(str(ev.get("url") or "")) == "identity_only":
        return "identity_only"
    if len(str(ev.get("search_snippet") or ev.get("snippet") or "")) >= 200:
        return "search_excerpt_only"
    return "minimal"


def compute_evidence_pipeline_stats(
    evidence: list[dict],
    citations: list[dict] | None = None,
    *,
    budget: Any = None,
    gate_reason: str = "",
    verified_quant: int | None = None,
) -> dict[str, Any]:
    """Structured pipeline diagnostics for critic, metrics, and failure reporting."""
    evs = evidence or []
    cites = citations or []
    retrieved = len(cites) if cites else len(evs)
    fetched = sum(1 for e in evs if _is_fetched(e))
    accessible = sum(1 for e in evs if _is_accessible(e))
    quant_high = sum(1 for e in evs if quant_signal_level(e) == "high")
    quant_medium = sum(1 for e in evs if quant_signal_level(e) == "medium")
    candidates = extract_quantitative_rows(evs, cites)
    quant_candidates = len(candidates)
    representations: dict[str, int] = {}
    for ev in evs:
        rep = classify_source_representation(ev)
        representations[rep] = representations.get(rep, 0) + 1

    degradation_risk = sum(
        1
        for e in evs
        if _is_fetched(e)
        and len(str(e.get("snippet") or "")) >= 1500
        and len(str(e.get("full_text") or "")) < 200
    )

    budget_exhausted = gate_reason == "insufficient_budget"
    if budget is not None:
        remaining = int(getattr(budget, "remaining_enrich_calls", 0) or 0)
        used = int(getattr(budget, "used_enrich_calls", 0) or 0)
    else:
        remaining = 0
        used = 0

    termination = infer_termination(
        retrieved=retrieved,
        accessible=accessible,
        quant_candidates=quant_candidates,
        verified_quant=verified_quant,
        budget_exhausted=budget_exhausted,
        degradation_risk=degradation_risk,
    )

    return {
        "retrieved": retrieved,
        "fetched": fetched,
        "accessible": accessible,
        "quant_signal_high": quant_high,
        "quant_signal_medium": quant_medium,
        "quant_candidates": quant_candidates,
        "verified_quant": verified_quant if verified_quant is not None else None,
        "representations": representations,
        "degradation_risk": degradation_risk,
        "failure_modes": {
            "evidence_degradation": degradation_risk > 0,
            "source_inaccessible": accessible < retrieved and fetched < retrieved,
            "budget_exhausted": budget_exhausted,
        },
        "budget": {
            "used_enrich_calls": used,
            "remaining_enrich_calls": remaining,
        },
        "termination": termination,
        "summary": pipeline_summary(
            retrieved=retrieved,
            fetched=fetched,
            accessible=accessible,
            quant_candidates=quant_candidates,
            verified_quant=verified_quant,
            termination=termination,
        ),
    }


def infer_termination(
    *,
    retrieved: int,
    accessible: int,
    quant_candidates: int,
    verified_quant: int | None,
    budget_exhausted: bool,
    degradation_risk: int,
) -> str:
    if retrieved == 0:
        return "no_relevant_sources"
    if degradation_risk > 0:
        return "evidence_degradation"
    if budget_exhausted:
        return "budget_exhausted"
    if accessible < max(1, retrieved // 2) and quant_candidates == 0:
        return "sources_inaccessible"
    if quant_candidates == 0:
        return "no_quantitative_evidence"
    if verified_quant is not None and verified_quant == 0 and quant_candidates > 0:
        return "extraction_unverified"
    return "sufficient_evidence"


def pipeline_summary(
    *,
    retrieved: int,
    fetched: int,
    accessible: int,
    quant_candidates: int,
    verified_quant: int | None,
    termination: str,
) -> str:
    if termination == "no_relevant_sources":
        return "No relevant sources were retrieved for this question."
    if termination == "evidence_degradation":
        return (
            "Some sources were fetched but their full text may not have been retained in the "
            "working evidence set (merge/state issue)."
        )
    if termination == "budget_exhausted":
        vq = verified_quant if verified_quant is not None else 0
        return (
            f"Research budget exhausted before all gaps closed: "
            f"{retrieved} cited · {fetched} deep-fetched · {accessible} with excerpt · "
            f"{quant_candidates} numeric mentions · {vq} verified in memo."
        )
    if termination == "sources_inaccessible":
        return (
            f"Identified {retrieved} relevant sources, but only {accessible} yielded accessible "
            "representations deep enough for quantitative extraction (DOI/abstract-only pages)."
        )
    if termination == "no_quantitative_evidence":
        return (
            f"Retrieved {retrieved} sources ({accessible} accessible), but none contained "
            "extractable quantitative evidence in collected excerpts."
        )
    if termination == "extraction_unverified":
        return (
            f"Found {quant_candidates} quantitative candidates in sources, but none passed "
            "grounded verification against cited excerpts."
        )
    return f"{retrieved} sources retrieved; {quant_candidates} quantitative candidates found."
