"""Re-fetch sources and verify claims without an LLM."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable
from urllib.parse import urlparse

from app.domain.adversarial import (
    infer_provenance,
    normalize_kind,
    quality_band_for,
    recalibrate_claim_confidence,
)
from app.domain.citations import host_of, quote_in_source
from app.domain.locator import locate_quote
from app.domain.metric_grounding import (
    assess_causal_delta,
    assess_claim_span_grounding,
    assess_scope_overgeneralization,
    assess_subject_scale_scope,
    assess_subject_topic_scope,
)
from app.domain.schema import Claim
from app.observability.logging import event

NUMBER_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)\s*%?")
ARXIV_ABS_RE = re.compile(r"arxiv\.org/(?:abs|html)/(\d{4}\.\d{4,5})", re.I)
MAX_REFETCH = 6
MIN_CACHED_CHARS = 400


def load_bearing_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for match in NUMBER_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        is_pct = match.group(0).strip().endswith("%")
        if raw.isdigit() and not is_pct and (len(raw) < 3 or 1900 <= int(raw) <= 2035):
            continue
        out.add(raw)
        if is_pct:
            out.add(raw + "%")
    return out


def arxiv_pdf_fallback(url: str) -> str:
    match = ARXIV_ABS_RE.search(url or "")
    if not match:
        return ""
    return f"https://arxiv.org/pdf/{match.group(1)}.pdf"


def _as_claim(claim: Claim | dict) -> dict:
    return claim if isinstance(claim, dict) else claim.model_dump(mode="json")


def _source_text(ev: dict) -> str:
    return (
        ev.get("full_text")
        or ev.get("text")
        or ev.get("content")
        or ev.get("quote")
        or ev.get("snippet")
        or ev.get("title")
        or ""
    )


def _apply(claim: Claim | dict, patch: dict) -> None:
    if isinstance(claim, dict):
        claim.update(patch)
        return
    for key, value in patch.items():
        if hasattr(claim, key):
            setattr(claim, key, value)


def verify_against_sources(
    claims: list,
    evidence: list[dict],
    citations: list[dict] | None = None,
    *,
    refetch: bool = True,
    fetch_fn: Callable[[str], str] | None = None,
    query: str = "",
) -> dict[str, Any]:
    """Attach locator + verification_status to each claim. Optionally refetch URLs."""
    by_url = {(e.get("url") or "").rstrip("/").lower(): e for e in evidence if e.get("url")}
    url_to_n = {
        (c.get("url") or "").rstrip("/").lower(): c.get("n")
        for c in (citations or [])
        if c.get("url")
    }
    fetched = 0
    sources: dict[str, dict] = {}
    edges: list[dict] = []
    compact_claims: list[dict] = []

    for claim in claims or []:
        data = _as_claim(claim)
        url = (data.get("url") or "").strip()
        key = url.rstrip("/").lower()
        ev = by_url.get(key) or {}
        text = _source_text(ev)
        if refetch and fetch_fn and url and len(text) < MIN_CACHED_CHARS and fetched < MAX_REFETCH:
            fetched += 1
            got = fetch_fn(url) or ""
            if len(got) < 80:
                alt = arxiv_pdf_fallback(url)
                if alt:
                    got = fetch_fn(alt) or ""
            if len(got) >= 80:
                text = got
                ev = {**ev, "url": url, "full_text": got, "title": ev.get("title") or data.get("text") or url}
                by_url[key] = ev
        locator = locate_quote(data.get("quote") or data.get("text") or "", text)
        numbers = load_bearing_numbers(data.get("text") or "")
        source_numbers = load_bearing_numbers(text)
        missing_nums = numbers - source_numbers if numbers else set()
        kind = normalize_kind(data.get("kind") or "", has_quote=bool(data.get("quote")))
        causal = assess_causal_delta(data.get("text") or "", text)
        scope = assess_scope_overgeneralization(data.get("text") or "", text)
        scale_scope = assess_subject_scale_scope(
            data.get("text") or "", text, query=query or ""
        )
        topic_scope = assess_subject_topic_scope(
            data.get("text") or "", text, query=query or ""
        )
        span_gate = assess_claim_span_grounding(
            data.get("text") or "",
            text,
            quote=str(data.get("quote") or ""),
            kind=str(kind or ""),
        )
        provenance = data.get("provenance") or infer_provenance(data.get("text") or "", text)
        if not url:
            status, note = "source_missing", "Claim has no source URL."
        elif len(text) < 40:
            status, note = "source_missing", "Source could not be retrieved."
        elif causal and causal.get("status") in {"wrong_causal", "wrong_number", "source_missing"}:
            status, note = str(causal["status"]), str(causal.get("note") or "Causal comparison not supported by source.")
        elif scope and scope.get("status") == "scope_bleed":
            status, note = "wrong_causal", str(scope.get("note") or "Scope overgeneralization.")
        elif scale_scope and scale_scope.get("status") == "scale_mismatch":
            status, note = "wrong_causal", str(
                scale_scope.get("note") or "Model-scale attribution not supported by source."
            )
        elif topic_scope and topic_scope.get("status") == "topic_mismatch":
            status, note = "unsupported", str(
                topic_scope.get("note") or "Source is off-scope for the asked subject."
            )
        elif locator.found and missing_nums:
            # The cited quote genuinely exists in the source, but the claim
            # asserts a number the source doesn't — a more specific, more
            # useful diagnosis than the generic "ungrounded" span_gate would
            # give below for the same claim (its number-mismatch check would
            # otherwise never get consulted, since span_gate about a claim
            # asserting the wrong number naturally reads as "no matching
            # span" too).
            status, note = "wrong_number", f"Numbers not found in source: {', '.join(sorted(missing_nums)[:4])}."
        elif span_gate and span_gate.get("status") == "ungrounded":
            status, note = "unsupported", str(span_gate.get("note") or "Claim lacks a grounded 1-2 sentence source span.")
        elif kind in {"inferred", "speculative", "recommendation"} and not locator.found:
            status, note = "inferred", "Marked as inference; not treated as a paper finding."
        elif not locator.found:
            status, note = "unsupported", "Quote/claim text was not found in the retrieved source."
        elif missing_nums:
            status, note = "wrong_number", f"Numbers not found in source: {', '.join(sorted(missing_nums)[:4])}."
        else:
            status, note = "verified", "Quote (or high-overlap span) found in the source."
            if causal and causal.get("status") == "ok":
                note = "Quote found; causal delta has supporting comparison cues in source windows."
            elif span_gate and span_gate.get("status") == "ok":
                note = "Claim grounded in a 1-2 sentence source span."

        patch = {
            "kind": kind,
            "locator": locator.label if locator.found else data.get("locator") or "",
            "verification_status": status,
            "verification_note": note,
            "provenance": provenance,
            "quality_band": data.get("quality_band")
            or quality_band_for(url, str(data.get("tier") or ev.get("tier") or ""), str(ev.get("title") or data.get("title") or "")),
            "url": url or data.get("url") or "",
        }
        if locator.found and not data.get("quote"):
            patch["quote"] = (ev.get("quote") or ev.get("snippet") or "")[:400]
        _apply(claim, patch)
        conf = recalibrate_claim_confidence(
            {**data, **patch, "confidence": data.get("confidence"), "provenance": provenance},
            evidence,
        )
        _apply(claim, {"confidence": conf, "kind": kind})
        data["confidence"] = conf
        data["kind"] = kind

        source_id = ""
        if url:
            source_id = "src_" + hashlib.sha1(key.encode()).hexdigest()[:12]
            sources[key] = {
                "id": source_id,
                "n": url_to_n.get(key),
                "url": url,
                "title": ev.get("title") or data.get("url") or url,
                "host": host_of(url) or urlparse(url).hostname or "",
                "tier": ev.get("tier") or data.get("tier") or "",
                "quality_band": quality_band_for(url, str(ev.get("tier") or data.get("tier") or ""), str(ev.get("title") or "")),
                "published": ev.get("published") or data.get("published") or "",
                "content": text[:40_000],
            }
            relation = "contradicts" if data.get("contradict_ids") and status != "verified" else "supports"
            if status in {"verified", "wrong_number", "wrong_causal", "inferred"}:
                relation = "supports" if status == "verified" else "qualifies"
            if data.get("contradict_ids") and ev.get("id") in (data.get("contradict_ids") or []):
                relation = "contradicts"
            edges.append(
                {
                    "claim_id": data.get("id") or "",
                    "source_id": source_id,
                    "relation": relation,
                    "locator": locator.as_dict() if locator.found else None,
                    "quote": (data.get("quote") or "")[:400],
                }
            )

        compact_claims.append(
            {
                "id": data.get("id") or "",
                "text": data.get("text") or "",
                "kind": kind,
                "confidence": data.get("confidence"),
                "verification_status": status,
                "verification_note": note,
                "provenance": provenance,
                "locator": locator.label if locator.found else "",
                "locator_kind": locator.kind if locator.found else "",
                "page": locator.page,
                "quote": (patch.get("quote") or data.get("quote") or "")[:400],
                "url": url,
                "n": url_to_n.get(key),
                "quality_band": patch["quality_band"],
                "support_ids": data.get("support_ids") or [],
                "contradict_ids": data.get("contradict_ids") or [],
            }
        )

    event("citation_verify", claims=len(compact_claims), refetch=fetched)
    return {
        "claims": compact_claims,
        "sources": [
            {**{k: v for k, v in src.items() if k != "content"}, "content_chars": len(src.get("content") or "")}
            for src in sources.values()
        ],
        "edges": edges,
        "_sources_full": list(sources.values()),
    }
