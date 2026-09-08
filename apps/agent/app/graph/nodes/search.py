from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.conf.thresholds import RetrievalThresholds
from app.domain.adaptive_code_ratio import adaptive_code_ratio, explain_code_ratio
from app.domain.research_depth import effective_depth
from app.domain.retrieval_limits import (
    API_RESULTS_PER_QUERY,
    FANOUT_CEILING,
    RANK_NO_PRIMARY_CAP,
    RANK_PRIMARY_CAP,
    RANK_SECONDARY_REST,
    SEARCH_QUERY_CAPS,
)
from app.domain.scholar_query import compact_retrieval_query
from app.tools.tavily_client import tavily
from app.domain.citations import is_citable_url
from app.domain.credibility import credibility_score
from app.domain.adversarial import retrieval_rank_score
from app.domain.research_intent import demote_secondary, is_secondary_host
from app.domain.schema import AgentName, SourceTier
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event, logger
from app.observability.node_trace import fanout_parallelism, trace_span
from app.tools import cache
from app.tools.retry import retry_call


def search_node(state: ResearchState) -> dict:
    if "search" not in (state.get("agents_to_run") or []):
        return {"evidence": [], "traces": [{"node": "search", "skipped": True, "external_calls": 0}]}
    if budget_from(state).remaining_retrieval_calls <= 0:
        return {"evidence": [], "traces": [{"node": "search", "skipped": "budget", "external_calls": 0}]}

    # Issue #2 fix: Adaptive code ratio based on query intent
    brief = state.get("brief") or {}
    query_type = brief.get("query_type") or brief.get("category")
    query_text = state.get("query") or ""
    max_code_ratio = adaptive_code_ratio(query_type, query_text)
    
    # Log adaptive ratio decision
    event("search_adaptive_code_ratio", {
        "query_type": query_type,
        "ratio": max_code_ratio,
        "explanation": explain_code_ratio(max_code_ratio, query_type)
    })

    questions = _questions(state, AgentName.SEARCH)
    parallel = fanout_parallelism(state, ceiling=FANOUT_CEILING)
    hits: list[dict] = []
    external_calls = 0
    trace_entries: list[dict[str, Any]] = []

    with trace_span("search", active_agent="search", parallel=parallel) as span:
        def run_one(question: str) -> tuple[str, list[dict], int]:
            rows, calls = _search(question, max_code_ratio=max_code_ratio)
            return question, rows[:API_RESULTS_PER_QUERY], calls

        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(run_one, q): q for q in questions}
            for future in as_completed(futures):
                question, rows, calls = future.result()
                hits.extend(rows)
                external_calls += calls
                top_tier = rows[0].get("tier") if rows else None
                trace_entries.append(
                    {
                        "node": "search",
                        "active_sub_query": question[:160],
                        "active_agent": "search",
                        "source_tier": top_tier,
                        "n": len(rows),
                        "external_calls": calls,
                    }
                )
        ranked = _rank_and_filter(hits, state.get("query") or "")
        if not ranked:
            extra: list[dict] = []
            for question in questions:
                extra.extend(_ddg(question)[:API_RESULTS_PER_QUERY])
                external_calls += 1
            ranked = _rank_and_filter(extra, state.get("query") or "")
        span["n"] = len(ranked)
        span["external_calls"] = external_calls
        if trace_entries:
            span["active_sub_query"] = trace_entries[-1].get("active_sub_query")
            span["source_tier"] = trace_entries[-1].get("source_tier")

    event("search", n=len(ranked))
    return {
        "evidence": ranked,
        "traces": trace_entries or [span],
    }


def _questions(state: ResearchState, agent: AgentName) -> list[str]:
    plan = state.get("plan") or {}
    subs = [s["question"] for s in plan.get("sub_queries", []) if s.get("agent") == agent.value]
    brief = state.get("brief") or {}
    depth = effective_depth(brief)
    limit = SEARCH_QUERY_CAPS.get(depth, 4)
    raw = subs or [state["query"]]
    return [compact_retrieval_query(q, goal=state.get("query") or "", agent=agent.value) for q in raw[:limit]]


def _classify_paper_domain(paper: dict) -> str:
    """Classify evidence into domain (shared with scholar.py for consistency).
    
    Returns: "code" | "benchmark" | "docs" | "theory"
    """
    url = paper.get("url", "").lower()
    title = paper.get("title", "").lower()
    snippet = paper.get("snippet", "").lower()
    blob = f"{url} {title} {snippet}"
    
    # Code: GitHub, GitLab, implementation
    if any(host in url for host in ["github.com", "gitlab.com", "bitbucket.org", "codeberg.org"]):
        return "code"
    if re.search(r"\b(implementation|source code|library|package|repository)\b", title):
        return "code"
    
    # Benchmark: evaluation, metrics, comparison
    if re.search(r"\b(benchmark|evaluation|comparison|leaderboard|ablation)\b", title):
        return "benchmark"
    if re.search(r"\b(metric|performance comparison|empirical study)\b", title):
        return "benchmark"
    
    # Docs: official documentation, API reference
    if any(host in url for host in ["docs.", "documentation", "api.", "developer."]):
        return "docs"
    if re.search(r"\b(documentation|api reference|guide|tutorial|manual)\b", title):
        return "docs"
    
    # Theory: papers, research, analysis
    if any(host in url for host in ["arxiv.org", "aclanthology.org", "openreview.net", "acm.org", "ieee.org"]):
        return "theory"
    
    return "theory"


def _balanced_evidence_pool(papers: list[dict], max_code_ratio: float = RetrievalThresholds.MAX_CODE_RATIO) -> list[dict]:
    """Enforce domain balance to prevent coding skew (same logic as scholar.py).
    
    Strategy (FIXED to prevent backfill violation):
    1. Take ALL non-code papers first (diverse evidence)
    2. Calculate code papers needed to reach max_code_ratio of final pool
    3. Never exceed max_code_ratio, even when source pool is heavily skewed
    
    Args:
        papers: Raw search results from Tavily/DDG
        max_code_ratio: Maximum fraction of code papers (default 40%)
    
    Returns:
        Balanced evidence pool with enforced diversity
    """
    if not papers:
        return []
    
    # Classify by domain
    code_papers = []
    theory_papers = []
    benchmark_papers = []
    doc_papers = []
    
    for p in papers:
        domain = _classify_paper_domain(p)
        if domain == "code":
            code_papers.append(p)
        elif domain == "theory":
            theory_papers.append(p)
        elif domain == "benchmark":
            benchmark_papers.append(p)
        else:
            doc_papers.append(p)
    
    total = len(papers)
    
    logger.info(
        f"search_domain_balance_before: total={total}, code={len(code_papers)}, "
        f"theory={len(theory_papers)}, benchmark={len(benchmark_papers)}, docs={len(doc_papers)}"
    )
    
    # NEW STRATEGY: Build balanced pool without backfill violation
    balanced = []
    balanced.extend(theory_papers)
    balanced.extend(benchmark_papers)
    balanced.extend(doc_papers)
    
    non_code_count = len(balanced)
    
    # Calculate max code papers to reach max_code_ratio
    if non_code_count > 0:
        max_code_count = int(non_code_count * (max_code_ratio / (1 - max_code_ratio)))
    else:
        max_code_count = int(total * max_code_ratio)
    
    balanced.extend(code_papers[:max_code_count])
    
    balanced_code = sum(1 for p in balanced if _classify_paper_domain(p) == "code")
    code_ratio = balanced_code / len(balanced) if balanced else 0
    logger.info(
        f"search_domain_balance_after: total={len(balanced)}, code={balanced_code}, "
        f"code_ratio={code_ratio:.2%}, target_max={max_code_ratio:.2%}"
    )
    
    return balanced


def _search(query: str, max_code_ratio: float = RetrievalThresholds.MAX_CODE_RATIO) -> tuple[list[dict], int]:
    key = cache.cache_key("search", query)
    hit = cache.get(key)
    if hit is not None:
        return hit, 0
    rows: list[dict] = []
    external_calls = 0
    if tavily.available:
        external_calls += 1
        rows = retry_call(lambda: _tavily(query), attempts=3, default=[]) or []
    if not rows:
        external_calls += 1
        rows = retry_call(lambda: _ddg(query), attempts=2, default=[]) or []
    
    # NEW: Apply domain balancing to prevent coding skew (now adaptive based on query type)
    balanced_rows = _balanced_evidence_pool(rows, max_code_ratio=max_code_ratio)
    
    return cache.put(key, balanced_rows), external_calls


def _tavily(query: str) -> list[dict]:
    return [
        _to_evidence(item.get("title", ""), item.get("url", ""), item.get("content", ""), AgentName.SEARCH)
        for item in tavily.search(query)
    ]


def _ddg(query: str) -> list[dict]:
    try:
        from ddgs import DDGS

        rows = DDGS().text(query, max_results=API_RESULTS_PER_QUERY)
    except Exception as exc:
        logger.warning("ddg_failed %s", exc)
        return []
    return [
        _to_evidence(
            item.get("title", ""),
            item.get("href", "") or item.get("url", ""),
            item.get("body", ""),
            AgentName.SEARCH,
        )
        for item in rows
    ]


def _rank_and_filter(hits: list[dict], query: str = "") -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for h in sorted(hits, key=lambda ev: retrieval_rank_score(ev, query), reverse=True):
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
        in {
            "official_regulation",
            "intergovernmental",
            "standard_body",
            "peer_reviewed",
            "specialist_research",
        }
        and not is_secondary_host(h.get("url") or "")
    ]
    if primary:
        rest = [h for h in unique if h not in primary][:RANK_SECONDARY_REST]
        return (primary + rest)[:RANK_PRIMARY_CAP]
    return unique[:RANK_NO_PRIMARY_CAP]


def _to_evidence(
    title: str,
    url: str,
    snippet: str,
    agent: AgentName,
    fallback: SourceTier | None = None,
) -> dict:
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
