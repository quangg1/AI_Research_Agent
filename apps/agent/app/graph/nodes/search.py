from __future__ import annotations

import hashlib
from typing import Any

import httpx

from app.config import settings
from app.domain.citations import is_citable_url
from app.domain.credibility import credibility_score
from app.domain.research_intent import authority_score, demote_secondary, is_secondary_host
from app.domain.schema import AgentName, SourceTier
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event, logger
from app.tools import cache
from app.tools.retry import retry_call


def search_node(state: ResearchState) -> dict:
    if "search" not in (state.get("agents_to_run") or []):
        return {"evidence": [], "traces": [{"node": "search", "skipped": True, "external_calls": 0}]}
    if budget_from(state).remaining_calls <= 0:
        return {"evidence": [], "traces": [{"node": "search", "skipped": "budget", "external_calls": 0}]}

    questions = _questions(state, AgentName.SEARCH)
    hits: list[dict] = []
    external_calls = 0
    for question in questions[:3]:
        rows, calls = _search(question)
        hits.extend(rows[:8])
        external_calls += calls
    ranked = _rank_and_filter(hits)
    if not ranked:
        extra: list[dict] = []
        for question in questions[:3]:
            extra.extend(_ddg(question)[:8])
            external_calls += 1
        ranked = _rank_and_filter(extra)
    event("search", n=len(ranked))
    return {
        "evidence": ranked,
        "traces": [{"node": "search", "n": len(ranked), "external_calls": external_calls}],
    }


def _questions(state: ResearchState, agent: AgentName) -> list[str]:
    plan = state.get("plan") or {}
    subs = [s["question"] for s in plan.get("sub_queries", []) if s.get("agent") == agent.value]
    return (subs or [state["query"]])[:4]


def _search(query: str) -> tuple[list[dict], int]:
    key = cache.cache_key("search", query)
    hit = cache.get(key)
    if hit is not None:
        return hit, 0
    rows: list[dict] = []
    external_calls = 0
    if settings.tavily_api_key:
        external_calls += 1
        rows = retry_call(lambda: _tavily(query), attempts=3, default=[]) or []
    if not rows:
        external_calls += 1
        rows = retry_call(lambda: _ddg(query), attempts=2, default=[]) or []
    return cache.put(key, rows), external_calls


def _tavily(query: str) -> list[dict]:
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 8,
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("tavily_failed %s", exc)
        return []
    return [_to_evidence(item.get("title", ""), item.get("url", ""), item.get("content", ""), AgentName.SEARCH) for item in data.get("results", [])]


def _ddg(query: str) -> list[dict]:
    try:
        from ddgs import DDGS

        rows = DDGS().text(query, max_results=10)
    except Exception as exc:
        logger.warning("ddg_failed %s", exc)
        return []
    return [
        _to_evidence(item.get("title", ""), item.get("href", "") or item.get("url", ""), item.get("body", ""), AgentName.SEARCH)
        for item in rows
    ]


def _rank_and_filter(hits: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for h in sorted(hits, key=authority_score, reverse=True):
        key = h.get("url") or h.get("id")
        if not key or key in seen:
            continue
        if h.get("url") and not is_citable_url(h.get("url") or ""):
            continue
        seen.add(key)
        unique.append(h)
    unique = demote_secondary(unique)
    primary = [
        h
        for h in unique
        if h.get("tier")
        in {"official_regulation", "intergovernmental", "standard_body", "peer_reviewed", "specialist_research"}
        and not is_secondary_host(h.get("url") or "")
    ]
    if primary:
        rest = [h for h in unique if h not in primary][:2]
        return (primary + rest)[:8]
    return unique[:5]


def _to_evidence(title: str, url: str, snippet: str, agent: AgentName, fallback: SourceTier | None = None) -> dict:
    tier, score = credibility_score(url, fallback=fallback)
    eid = "ev_" + hashlib.sha1((url or title or snippet[:40]).encode()).hexdigest()[:10]
    return {
        "id": eid,
        "title": title or "Untitled",
        "url": url,
        "snippet": (snippet or "")[:1200],
        "quote": (snippet or "")[:400],
        "source_agent": agent.value,
        "tier": tier.value,
        "credibility": score,
        "published": "",
    }
