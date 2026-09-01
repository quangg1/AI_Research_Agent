"""FACT-lite: verify inline [n] citations against fetched evidence (no external judge)."""

from __future__ import annotations

import re

from app.domain.citations import quote_in_source

_CITE_N_RE = re.compile(r"\[(\d+)\]")
_MAX_CHECKS = 20


def _evidence_blob(ev: dict) -> str:
    return ev.get("full_text") or ev.get("quote") or ev.get("snippet") or ev.get("title") or ""


def _ledger_blob(citation: dict) -> str:
    return citation.get("quote") or citation.get("title") or ""


def verify_memo_citations(
    markdown: str,
    citations: list[dict],
    evidence: list[dict],
) -> dict:
    """Check top inline cites map to a verifiable span in evidence or ledger quote."""
    text = markdown or ""
    by_n = {int(c["n"]): c for c in citations if c.get("n") is not None}
    by_url = {
        (ev.get("url") or "").strip().rstrip("/").lower(): ev for ev in evidence if ev.get("url")
    }

    seen: set[int] = set()
    checked = 0
    verified = 0
    failures: list[str] = []

    for match in _CITE_N_RE.finditer(text):
        n = int(match.group(1))
        if n in seen or n not in by_n:
            continue
        seen.add(n)
        if checked >= _MAX_CHECKS:
            break
        checked += 1
        cite = by_n[n]
        url = (cite.get("url") or "").strip().rstrip("/").lower()
        ev = by_url.get(url) or {}
        source = _evidence_blob(ev) or _ledger_blob(cite)
        quote = (cite.get("quote") or "").strip()
        ok = bool(source) and (
            (quote and quote_in_source(quote, source))
            or (not quote and url in by_url)
        )
        if ok:
            verified += 1
        else:
            failures.append(f"[{n}] missing or unverifiable quote for {cite.get('title') or url or 'source'}")

    accuracy = round(verified / max(checked, 1), 3)
    return {
        "checked": checked,
        "verified": verified,
        "accuracy": accuracy,
        "failures": failures,
    }
