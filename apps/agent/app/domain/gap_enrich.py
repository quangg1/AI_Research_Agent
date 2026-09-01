from __future__ import annotations

import re
from typing import Any

from app.domain.schema import Budget
from app.tools.fetch import evidence_from_url, is_fetchable, sanitize_fetched_content

_PRIMARY_HOST_MARKERS = ("arxiv.org", "doi.org", "github.com", "gitlab.com", "openreview.net")
_OFFICIAL_DOC_MARKERS = ("docs.", ".gov", "openai.com", "anthropic.com", "google.com", "microsoft.com")
_THIN_TEXT_CHARS = 1600


def coverage_slots_from_state(state: dict[str, Any]) -> list[dict]:
    brief = state.get("brief") or {}
    slots = brief.get("must_answer") or []
    if slots:
        return [dict(s) for s in slots]
    critic = state.get("critic") or {}
    cov = critic.get("coverage") or {}
    return [dict(s) for s in (cov.get("slots") or [])]


def gap_slots(slots: list[dict]) -> list[dict]:
    """Critical open/weak first, then other open/weak dimensions."""
    ordered: list[dict] = []
    seen: set[str] = set()
    for critical in (True, False):
        for slot in slots:
            sid = str(slot.get("id") or "")
            if slot.get("status") not in {"open", "weak"}:
                continue
            if bool(slot.get("critical")) != critical:
                continue
            if sid in seen:
                continue
            seen.add(sid)
            ordered.append(slot)
    return ordered


def urls_for_gap_slots(
    evidence: list[dict],
    slots: list[dict],
    *,
    limit: int = 5,
) -> list[str]:
    """URLs worth full-text fetch to close open/weak must-answer slots."""
    if not slots:
        return []
    by_id = {e.get("id"): e for e in evidence if e.get("id")}
    urls: list[str] = []
    seen: set[str] = set()

    for slot in gap_slots(slots):
        for eid in slot.get("evidence_ids") or []:
            ev = by_id.get(eid)
            if not ev:
                continue
            url = (ev.get("url") or "").strip()
            if not url or not is_fetchable(url) or url in seen:
                continue
            text_len = len(ev.get("full_text") or ev.get("snippet") or "")
            if text_len >= _THIN_TEXT_CHARS:
                continue
            urls.append(url)
            seen.add(url)
            if len(urls) >= limit:
                return urls

    for slot in gap_slots(slots):
        for ev in _evidence_for_slot(evidence, slot):
            url = (ev.get("url") or "").strip()
            if not url or not is_fetchable(url) or url in seen:
                continue
            text_len = len(ev.get("full_text") or ev.get("snippet") or "")
            if text_len >= _THIN_TEXT_CHARS:
                continue
            urls.append(url)
            seen.add(url)
            if len(urls) >= limit:
                return urls

    for ev in _rank_primary_candidates(evidence):
        url = (ev.get("url") or "").strip()
        if not url or not is_fetchable(url) or url in seen:
            continue
        if len(ev.get("full_text") or ev.get("snippet") or "") >= _THIN_TEXT_CHARS:
            continue
        urls.append(url)
        seen.add(url)
        if len(urls) >= limit:
            break
    return urls


def fetch_gap_evidence(
    evidence: list[dict],
    slots: list[dict],
    budget: Budget,
    *,
    limit: int = 3,
) -> tuple[list[dict], int]:
    """Fetch full text for gap-targeted URLs; returns rows to merge into evidence."""
    urls = urls_for_gap_slots(evidence, slots, limit=limit)
    fetched: list[dict] = []
    calls = 0
    for url in urls:
        if budget.remaining_enrich_calls <= 0 or calls >= limit:
            break
        existing = next((e for e in evidence if e.get("url") == url), {})
        if len(existing.get("full_text") or existing.get("snippet") or "") >= _THIN_TEXT_CHARS:
            continue
        row = evidence_from_url(url, title=existing.get("title") or "")
        budget.used_enrich_calls += 1
        budget.sync_totals()
        calls += 1
        if not row:
            continue
        if row.get("full_text"):
            row["full_text"] = sanitize_fetched_content(str(row["full_text"]))
        if row.get("snippet"):
            row["snippet"] = sanitize_fetched_content(str(row["snippet"]))[:1600]
        fetched.append(row)
    return fetched, calls


def merge_fetched_evidence(working: list[dict], fetched: list[dict]) -> list[dict]:
    by_url = {(e.get("url") or "").rstrip("/").lower(): i for i, e in enumerate(working)}
    out = [dict(e) for e in working]
    for row in fetched:
        url = (row.get("url") or "").rstrip("/").lower()
        if not url:
            continue
        if url in by_url:
            idx = by_url[url]
            prev = out[idx]
            out[idx] = {**prev, **{k: v for k, v in row.items() if v not in (None, "")}}
        else:
            out.append(dict(row))
            by_url[url] = len(out) - 1
    return out


def _evidence_for_slot(evidence: list[dict], slot: dict) -> list[dict]:
    patterns = [p for p in (slot.get("patterns") or []) if p]
    topic_terms = [t for t in (slot.get("topic_terms") or []) if t]
    hits: list[tuple[int, dict]] = []
    for ev in evidence:
        blob = " ".join(
            str(ev.get(key) or "")
            for key in ("title", "snippet", "quote")
        ) + " " + str(ev.get("full_text") or "")[:2500]
        aspect = sum(1 for p in patterns if re.search(p, blob, re.I))
        topic = sum(1 for t in topic_terms if t.lower() in blob.lower())
        if aspect <= 0 and topic < 1:
            continue
        hits.append((aspect * 2 + topic + _host_bonus(ev.get("url") or ""), ev))
    hits.sort(key=lambda pair: pair[0], reverse=True)
    return [ev for _, ev in hits]


def _rank_primary_candidates(evidence: list[dict]) -> list[dict]:
    ranked = sorted(evidence, key=lambda ev: _host_bonus(ev.get("url") or ""), reverse=True)
    return ranked


def _host_bonus(url: str) -> int:
    low = url.lower()
    if any(m in low for m in _PRIMARY_HOST_MARKERS):
        return 4
    if any(m in low for m in _OFFICIAL_DOC_MARKERS):
        return 2
    return 0
