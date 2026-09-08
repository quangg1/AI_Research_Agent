"""LLM-assisted verbatim quote extraction before attaching citations."""

from __future__ import annotations

from app.domain.citations import pick_quote, quote_in_source
from app.llm.client import CreditsExhaustedError, llm

MAX_BATCH = 8
MIN_QUOTE_LEN = 24


def _as_dict(claim) -> dict:
    return claim if isinstance(claim, dict) else claim.model_dump(mode="json")


def _set_quote(claim, quote: str) -> None:
    if isinstance(claim, dict):
        claim["quote"] = quote
    elif hasattr(claim, "quote"):
        claim.quote = quote


def _needs_quote(claim: dict) -> bool:
    quote = (claim.get("quote") or "").strip()
    if len(quote) >= MIN_QUOTE_LEN:
        return False
    if claim.get("kind") in {"inferred", "speculative", "recommendation"}:
        return False
    return bool(claim.get("url") or claim.get("text"))


def _evidence_for_claim(claim: dict, evidence: list[dict], citations: list[dict]) -> dict:
    url = (claim.get("url") or "").strip().rstrip("/").lower()
    for ev in evidence:
        if (ev.get("url") or "").strip().rstrip("/").lower() == url:
            return ev
    support = claim.get("support_ids") or []
    for ev in evidence:
        if ev.get("id") in support:
            return ev
    for c in citations:
        if (c.get("url") or "").strip().rstrip("/").lower() == url:
            return {
                "url": c.get("url"),
                "title": c.get("title"),
                "quote": c.get("quote"),
                "snippet": c.get("quote"),
            }
    return {}


def _source_blob(ev: dict) -> str:
    return (ev.get("full_text") or ev.get("text") or ev.get("content") or ev.get("quote") or ev.get("snippet") or ev.get("title") or "")


def _quote_prompt(batch: list[dict]) -> str:
    lines = []
    for item in batch:
        lines.append(
            f"- claim_id={item['claim_id']}\n"
            f"  claim: {item['text']}\n"
            f"  source: {item['source_excerpt'][:1800]}"
        )
    return (
        "For each claim, extract a verbatim supporting quote (12–280 chars) copied from the source excerpt. "
        "If no span supports the claim, set quote to null. JSON only: "
        '{"quotes": [{"claim_id": str, "quote": str|null}]}.\n\n'
        + "\n".join(lines)
    )


def enrich_claim_quotes_llm(
    claims: list,
    evidence: list[dict],
    citations: list[dict] | None = None,
) -> tuple[list, dict]:
    """Fill missing claim quotes via heuristic pick, then LLM extraction + verify."""
    stats = {"attempted": 0, "verified": 0, "heuristic": 0, "failed": 0}
    if not claims:
        return claims, stats

    pending: list[tuple[int, dict]] = []
    for i, claim in enumerate(claims):
        data = _as_dict(claim)
        if _needs_quote(data):
            pending.append((i, data))

    if not pending:
        return claims, stats

    pending = pending[:MAX_BATCH]
    stats["attempted"] = len(pending)

    for idx, data in pending:
        ev = _evidence_for_claim(data, evidence, citations or [])
        blob = _source_blob(ev)
        if not blob:
            continue
        heuristic = pick_quote(ev)
        if heuristic and quote_in_source(heuristic, blob):
            _set_quote(claims[idx], heuristic[:400])
            stats["heuristic"] += 1

    still: list[tuple[int, dict]] = []
    for idx, data in pending:
        if _needs_quote(_as_dict(claims[idx])):
            still.append((idx, data))

    if not still or not llm.available:
        return claims, stats

    batch = []
    for idx, data in still:
        ev = _evidence_for_claim(data, evidence, citations or [])
        batch.append(
            {
                "claim_id": data.get("id") or f"C{idx + 1}",
                "text": (data.get("text") or "")[:400],
                "source_excerpt": _source_blob(ev)[:2000],
                "url": data.get("url") or "",
            }
        )

    try:
        payload = llm.generate_json(
            prompt=_quote_prompt(batch),
            system="Extract verbatim supporting quotes. JSON only. Do not paraphrase.",
            max_tokens=2048,
        )
    except CreditsExhaustedError:
        raise
    except Exception:
        stats["failed"] += len(still)
        return claims, stats

    quotes: dict[str, str] = {}
    if isinstance(payload, dict):
        for item in payload.get("quotes") or []:
            if isinstance(item, dict) and item.get("claim_id") and item.get("quote"):
                quotes[str(item["claim_id"])] = str(item["quote"]).strip()

    for idx, data in still:
        cid = data.get("id") or f"C{idx + 1}"
        quote = quotes.get(cid, "")
        if not quote:
            stats["failed"] += 1
            continue
        ev = _evidence_for_claim(data, evidence, citations or [])
        if quote_in_source(quote, _source_blob(ev)):
            _set_quote(claims[idx], quote[:400])
            stats["verified"] += 1
        else:
            stats["failed"] += 1

    return claims, stats
