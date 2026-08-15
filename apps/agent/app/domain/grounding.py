from __future__ import annotations

import re

from app.domain.citations import quote_in_source
from app.domain.schema import Claim

TOKEN_RE = re.compile(r"[a-z0-9]{3,}")

FORBIDDEN = [
    re.compile(r"bigger model always (wins|beats|better)", re.I),
    re.compile(r"rag always requires (a )?vector (db|database)", re.I),
    re.compile(r"fine-?tun(e|ing) is always better than rag", re.I),
    re.compile(r"llm-as-judge is (unbiased|ground truth)", re.I),
    re.compile(r"multi-?agent always beats (a )?single agent", re.I),
]


def token_set(text: str) -> set[str]:
    return set(TOKEN_RE.findall((text or "").lower()))


def overlap_ratio(quote: str, source: str) -> float:
    q = token_set(quote)
    s = token_set(source)
    if not q or not s:
        return 0.0
    return len(q & s) / max(len(q), 1)


def evidence_blob(ev: dict) -> str:
    return " ".join(
        str(ev.get(k) or "")
        for k in ("title", "snippet", "quote", "full_text")
    )


def verify_claim(claim: Claim | dict, evidence: list[dict]) -> dict:
    data = claim if isinstance(claim, dict) else claim.model_dump(mode="json")
    by_id = {e.get("id"): e for e in evidence}
    support = [by_id[i] for i in data.get("support_ids") or [] if i in by_id]
    quote = data.get("quote") or ""
    text = data.get("text") or ""
    blob = " ".join(evidence_blob(e) for e in support) if support else ""
    quote_ok = quote_in_source(quote, blob) if quote and blob else False
    overlap_ok = overlap_ratio(quote or text, blob) >= 0.35 if blob else False
    grounded = bool(support) and (quote_ok or overlap_ok)
    for pat in FORBIDDEN:
        if pat.search(text):
            grounded = False
            data.setdefault("caveats", []).append(
                "Blocked: claim repeats an AI folklore myth (size, vectors, judges, multi-agent)."
            )
            data["confidence"] = min(float(data.get("confidence") or 0.4), 0.25)
    if not grounded:
        data["confidence"] = min(float(data.get("confidence") or 0.4), 0.35)
        data.setdefault("caveats", []).append("Quote is not attested in the cited source.")
    else:
        primary = support[0]
        data["url"] = data.get("url") or primary.get("url") or ""
        data["tier"] = data.get("tier") or primary.get("tier") or ""
        if not data.get("quote"):
            data["quote"] = (primary.get("quote") or primary.get("snippet") or "")[:400]
        data["confidence"] = max(float(data.get("confidence") or 0.5), float(primary.get("credibility") or 0.5) * 0.85)
    data["grounded"] = grounded
    return data


def verify_claims(claims: list, evidence: list[dict]) -> list[dict]:
    out = [verify_claim(c, evidence) for c in claims]
    kept = [c for c in out if c.get("grounded") or c.get("contradict_ids")]
    return kept or out[:1]
