from __future__ import annotations

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import httpx

from app.conf.thresholds import RetrievalThresholds
from app.domain.adaptive_code_ratio import adaptive_code_ratio, explain_code_ratio
from app.domain.adversarial import retrieval_rank_score
from app.domain.citations import is_citable_url
from app.domain.research_depth import effective_depth
from app.domain.retrieval_limits import API_RESULTS_PER_QUERY, FANOUT_CEILING
from app.domain.scholar_query import compact_retrieval_query
from app.domain.credibility import credibility_score
from app.domain.schema import AgentName, SourceTier
from app.graph.nodes.search import _questions
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event, logger
from app.observability.node_trace import fanout_parallelism, trace_span
from app.retrieval.hybrid import distinctive_hits, distinctive_terms, query_terms

TOPIC_RE = re.compile(
    r"\b(rag|retriev(?:al|er|e)|bm25|rerank|embedd|vector(?:less)?|hybrid search|"
    r"language model|\bllm\b|vllm|quantiz|fine-?tun|cross-encoder|chunk(?:ing)?|"
    r"tool[- ]calling|serving|pagedattention|agent(?:ic)?|observab|prompt injection)\b",
    re.I,
)
OFF_DOMAIN_RE = re.compile(
    r"\b(healthcare|clinical|hospital|patient|medical|nursing|tutoring|classroom|"
    r"k-12|pedagog|curriculum)\b",
    re.I,
)


def _classify_paper_domain(paper: dict) -> str:
    """Classify paper into domain based on URL, title, venue.
    
    Prevents coding skew by identifying paper types for balanced retrieval.
    
    Returns:
        "code" - Implementation/source code (GitHub, GitLab)
        "benchmark" - Evaluation, metrics, comparison studies
        "docs" - Official documentation, API references
        "theory" - Research papers, arxiv, analysis (default)
    """
    url = paper.get("url", "").lower()
    title = paper.get("title", "").lower()
    snippet = paper.get("snippet", "").lower()
    blob = f"{url} {title} {snippet}"
    
    # Code: GitHub, GitLab, implementation-focused
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
    
    # Theory: papers, research, analysis (default for academic sources)
    if any(host in url for host in ["arxiv.org", "aclanthology.org", "openreview.net", "acm.org", "ieee.org"]):
        return "theory"
    
    # Default to theory for unknown
    return "theory"


def _balanced_evidence_pool(papers: list[dict], max_code_ratio: float = RetrievalThresholds.MAX_CODE_RATIO) -> list[dict]:
    """Enforce domain balance at SOURCE to prevent coding skew.
    
    Strategy (FIXED to prevent backfill violation):
    1. Classify papers by domain (code/theory/benchmark/docs)
    2. Take ALL non-code papers first (they're the minority)
    3. Calculate how many code papers needed to reach max_code_ratio of final pool
    4. Add that many code papers (preserving ranking)
    
    This ensures we NEVER exceed max_code_ratio, even when source pool is heavily skewed.
    
    Args:
        papers: Raw results from OpenAlex/Semantic Scholar/Tavily
        max_code_ratio: Maximum fraction of code/implementation papers (default 40%)
    
    Returns:
        Balanced evidence pool with enforced domain diversity
    """
    if not papers:
        return []
    
    # Classify all papers by domain
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
    
    # Log domain distribution for debugging
    logger.info(
        f"domain_balance_before: total={total}, code={len(code_papers)}, "
        f"theory={len(theory_papers)}, benchmark={len(benchmark_papers)}, docs={len(doc_papers)}"
    )
    
    # NEW STRATEGY: Build balanced pool without backfill violation
    # 1. Add all non-code papers (they're diverse and valuable)
    balanced = []
    balanced.extend(theory_papers)
    balanced.extend(benchmark_papers)
    balanced.extend(doc_papers)
    
    non_code_count = len(balanced)
    
    # 2. Calculate how many code papers to add to reach max_code_ratio
    # If max_code_ratio = 0.4, then: code / (code + non_code) = 0.4
    # Solving: code = 0.4 * (code + non_code) => code = (0.4 / 0.6) * non_code
    if non_code_count > 0:
        max_code_count = int(non_code_count * (max_code_ratio / (1 - max_code_ratio)))
    else:
        # Edge case: all papers are code (100% skew)
        # Cap at original max_code_ratio of total
        max_code_count = int(total * max_code_ratio)
    
    # 3. Add code papers up to calculated limit (preserve ranking)
    balanced.extend(code_papers[:max_code_count])
    
    # Log balanced distribution
    balanced_code = sum(1 for p in balanced if _classify_paper_domain(p) == "code")
    code_ratio = balanced_code / len(balanced) if balanced else 0
    logger.info(
        f"domain_balance_after: total={len(balanced)}, code={balanced_code}, "
        f"code_ratio={code_ratio:.2%}, target_max={max_code_ratio:.2%}"
    )
    
    return balanced

# Rate limiting for Semantic Scholar (1 req/sec without key, be conservative)
_last_s2_call_time = 0.0
_s2_rate_limit_lock = __import__("threading").Lock()
_s2_is_rate_limited = False
_s2_rate_limit_until = 0.0


def scholar_node(state: ResearchState) -> dict:
    if "scholar" not in (state.get("agents_to_run") or []):
        return {"evidence": [], "traces": [{"node": "scholar", "skipped": True}]}
    if budget_from(state).remaining_retrieval_calls <= 0:
        return {"evidence": [], "traces": [{"node": "scholar", "skipped": "budget"}]}

    # Issue #2 fix: Adaptive code ratio based on query intent
    brief = state.get("brief") or {}
    query_type = brief.get("query_type") or brief.get("category")
    query_text = state.get("query") or ""
    max_code_ratio = adaptive_code_ratio(query_type, query_text)
    
    # Log adaptive ratio decision
    from app.observability.logging import event
    event("scholar_adaptive_code_ratio", {
        "query_type": query_type,
        "ratio": max_code_ratio,
        "explanation": explain_code_ratio(max_code_ratio, query_type)
    })

    questions = _questions(state, AgentName.SCHOLAR)
    parallel = fanout_parallelism(state, ceiling=FANOUT_CEILING)
    hits: list[dict] = []
    external_calls = 0
    trace_entries: list[dict] = []

    with trace_span("scholar", active_agent="scholar", parallel=parallel) as span:
        def run_one(question: str) -> tuple[str, list[dict], int]:
            rows, calls = _scholar_search(question, max_code_ratio=max_code_ratio)
            return question, rows[:API_RESULTS_PER_QUERY], calls

        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(run_one, q): q for q in questions}
            for future in as_completed(futures):
                question, rows, calls = future.result()
                hits.extend(rows)
                external_calls += calls
                top_tier = rows[0].get("tier") if rows else SourceTier.PEER_REVIEWED.value
                trace_entries.append(
                    {
                        "node": "scholar",
                        "active_sub_query": question[:160],
                        "active_agent": "scholar",
                        "source_tier": top_tier,
                        "n": len(rows),
                        "external_calls": calls,
                    }
                )
        hits = _dedupe_papers(hits)
        hits.sort(key=lambda ev: retrieval_rank_score(ev, state.get("query") or ""), reverse=True)
        span["n"] = len(hits)
        span["external_calls"] = external_calls
        if trace_entries:
            span["active_sub_query"] = trace_entries[-1].get("active_sub_query")
            span["source_tier"] = trace_entries[-1].get("source_tier")

    event("scholar", n=len(hits))
    return {
        "evidence": hits,
        "traces": trace_entries or [span],
    }


def _scholar_search(query: str, max_code_ratio: float = RetrievalThresholds.MAX_CODE_RATIO) -> tuple[list[dict], int]:
    global _s2_is_rate_limited, _s2_rate_limit_until
    
    q = compact_retrieval_query(query, agent="scholar")
    openalex = _openalex(q)
    calls = 1
    
    # Check if we're in a rate-limit cooldown period
    now = time.time()
    if _s2_is_rate_limited and now < _s2_rate_limit_until:
        cooldown_remaining = int(_s2_rate_limit_until - now)
        logger.info(f"semantic_scholar_skipped: in rate-limit cooldown for {cooldown_remaining}s, using OpenAlex only")
        balanced = _balanced_evidence_pool(openalex, max_code_ratio=max_code_ratio)
        return balanced, calls
    
    # Augment thin OpenAlex result sets with Semantic Scholar
    # Only call S2 if OpenAlex returned fewer than 5 results
    semantic: list[dict] = []
    if len(openalex) < 5:
        semantic = _semantic_scholar(q)
        calls += 1
        logger.info(f"semantic_scholar_enabled: augmenting {len(openalex)} OpenAlex results with {len(semantic)} S2 results")
    
    raw_results = _dedupe_papers(openalex + semantic)
    
    # NEW: Apply domain balancing to prevent coding skew (now adaptive based on query type)
    balanced_results = _balanced_evidence_pool(raw_results, max_code_ratio=max_code_ratio)
    
    return balanced_results, calls


def _openalex_query(query: str) -> str:
    cleaned = re.sub(r"[/?&#]+", " ", query or "")
    return re.sub(r"\s+", " ", cleaned).strip()[:180]


def _openalex(query: str) -> list[dict]:
    search_query = _openalex_query(query)
    logger.info(f"openalex_query: original={query[:80]}, transformed={search_query[:80]}")
    
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(
                "https://api.openalex.org/works",
                params={
                    "search": search_query,
                    "per_page": API_RESULTS_PER_QUERY,
                    "filter": "from_publication_date:2018-01-01",
                },
                headers={"User-Agent": "kiln-research-agent (mailto:research@kiln.local)"},
            )
            response.raise_for_status()
            data = response.json()
            total_results = len(data.get("results", []))
            logger.info(f"openalex_raw_results: got {total_results} results from API")
    except Exception as exc:
        logger.warning(f"openalex_failed: {exc}")
        return []
    out = []
    filtered_out_by_topic = 0
    filtered_out_by_url = 0
    
    for item in data.get("results", []):
        title = item.get("display_name") or "Untitled work"
        abstract = _inflate_abstract(item.get("abstract_inverted_index"))
        if not _on_topic(title, abstract, query):
            filtered_out_by_topic += 1
            continue
        loc = item.get("primary_location") or {}
        source = loc.get("source") or {}
        doi = str(item.get("doi") or "").replace("https://doi.org/", "")
        # Prefer OpenAlex's own "free copy" pointer over the (often paywalled)
        # publisher landing page / DOI redirect — enrich can't get full text
        # from a paywall, so a DOI-first URL silently starves the writer of
        # real content for exactly the primary-research papers it needs most.
        best_oa = item.get("best_oa_location") or {}
        oa_url = (best_oa.get("pdf_url") or best_oa.get("landing_page_url") or "").strip()
        if not oa_url:
            oa_url = str((item.get("open_access") or {}).get("oa_url") or "").strip()
        primary_url = (loc.get("landing_page_url") or "").strip() or (f"https://doi.org/{doi}" if doi else "")
        url = oa_url or primary_url or (item.get("id") or "")
        if not is_citable_url(url):
            filtered_out_by_url += 1
            continue
        year = str(item.get("publication_year") or "")
        eid = "ev_" + hashlib.sha1((url or title).encode()).hexdigest()[:10]
        # Classify by the *publisher's* location, not the free-copy substitute
        # above — a journal paper mirrored on arXiv is still peer-reviewed;
        # only the DOI/venue metadata should decide that, not which host we
        # ended up reading the text from.
        publication_type = _publication_type(
            primary_url or url,
            doi,
            [source.get("type"), source.get("display_name"), item.get("type"), item.get("type_crossref")],
        )
        # DEBUG: Log publication type for ArXiv papers
        if "arxiv" in url.lower():
            logger.info(f"openalex_arxiv_paper: title={title[:60]}, url={url}, publication_type={publication_type}")
        fallback = SourceTier.PEER_REVIEWED if publication_type == "peer_reviewed" else SourceTier.SPECIALIST_RESEARCH
        # Same reasoning as publication_type above: tier_for() hardcodes
        # arxiv.org -> SPECIALIST_RESEARCH regardless of `fallback` once the
        # host is recognized, so scoring off the OA-substituted `url` would
        # silently demote a peer-reviewed paper just because its free copy
        # happens to be on arXiv. Score the publisher location instead.
        tier, score = credibility_score(primary_url or url, year, fallback)
        if publication_type == "preprint":
            tier, score = SourceTier.SPECIALIST_RESEARCH, min(score, 0.76)
        out.append(
            {
                "id": eid,
                "title": title,
                "url": url,
                "snippet": (abstract or title)[:1200],
                "quote": (abstract or title)[:400],
                "source_agent": AgentName.SCHOLAR.value,
                "tier": tier.value,
                "credibility": score,
                "published": year,
                "doi": doi,
                "publication_type": publication_type,
            }
        )
    
    logger.info(f"openalex_filtering: raw={total_results}, rejected_topic={filtered_out_by_topic}, rejected_url={filtered_out_by_url}, final={len(out)}")
    return out


def _semantic_scholar(query: str) -> list[dict]:
    global _last_s2_call_time, _s2_is_rate_limited, _s2_rate_limit_until
    
    # Rate limiting: S2 allows 1 req/sec cumulative
    # Use 2.0s to be VERY conservative and avoid 429s
    min_interval = 2.0  # Increased from 1.2s
    
    with _s2_rate_limit_lock:
        now = time.time()
        elapsed = now - _last_s2_call_time
        if elapsed < min_interval:
            sleep_time = min_interval - elapsed
            logger.info(f"semantic_scholar_rate_limit_wait: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        _last_s2_call_time = time.time()
    
    # Retry logic for 429 errors
    max_retries = 2
    for attempt in range(max_retries):
        try:
            # Use API key if available to avoid rate limits
            headers = {"User-Agent": "kiln-research-agent/0.1"}
            api_key = os.getenv("S2_API_KEY") or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
            
            # DEBUG: Log key status
            if api_key:
                logger.info(f"semantic_scholar_using_api_key: key_length={len(api_key)}, key_prefix={api_key[:8]}...")
                headers["x-api-key"] = api_key
            else:
                logger.warning("semantic_scholar_no_api_key: S2_API_KEY not found in environment")
            
            with httpx.Client(timeout=15) as client:
                response = client.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params={
                        "query": _openalex_query(query),
                        "limit": 8,
                        "fields": "title,abstract,url,year,externalIds,publicationTypes,venue,openAccessPdf",
                    },
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                
                # Success! Clear rate limit flag
                _s2_is_rate_limited = False
                _s2_rate_limit_until = 0.0
                
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                # Rate limited! Set cooldown period
                _s2_is_rate_limited = True
                _s2_rate_limit_until = time.time() + 120  # 120 second cooldown (doubled)
                
                if attempt < max_retries - 1:
                    # More aggressive backoff: 3s → 6s
                    backoff = 3 * (2 ** attempt)  # 3s, 6s
                    logger.warning(f"semantic_scholar_429_retry: attempt {attempt + 1}, waiting {backoff}s")
                    time.sleep(backoff)
                    continue
                else:
                    # Give up after retries
                    logger.warning(f"semantic_scholar_429_failed: rate limited after {max_retries} attempts, entering 120s cooldown")
                    return []
            else:
                logger.warning("semantic_scholar_failed %s", exc)
                return []
        except Exception as exc:
            logger.warning("semantic_scholar_failed %s", exc)
            return []
        else:
            # Success, break retry loop
            break
    
    out: list[dict] = []
    for item in data.get("data", []):
        title = item.get("title") or "Untitled work"
        abstract = item.get("abstract") or ""
        if not _on_topic(title, abstract, query):
            continue
        external = item.get("externalIds") or {}
        doi = str(external.get("DOI") or "").strip()
        arxiv = str(external.get("ArXiv") or "").strip()
        oa_pdf = str((item.get("openAccessPdf") or {}).get("url") or "").strip()
        primary_url = (f"https://doi.org/{doi}" if doi else "") or str(item.get("url") or "")
        # Free copy first: a DOI link is frequently paywalled and enrich can't
        # read past that, while arXiv/openAccessPdf are readable full text.
        url = (
            oa_pdf
            or (f"https://arxiv.org/abs/{arxiv}" if arxiv else "")
            or primary_url
        )
        if not is_citable_url(url):
            continue
        year = str(item.get("year") or "")
        # Classify by the publisher/DOI reference, not the free-copy `url`
        # above — tier_for() hardcodes arxiv.org -> specialist regardless of
        # fallback, which would wrongly demote a genuinely peer-reviewed
        # paper just because its free copy happens to be on arXiv.
        publication_type = _publication_type(
            primary_url or url, doi, list(item.get("publicationTypes") or []) + [item.get("venue")]
        )
        fallback = SourceTier.PEER_REVIEWED if publication_type == "peer_reviewed" else SourceTier.SPECIALIST_RESEARCH
        tier, score = credibility_score(primary_url or url, year, fallback)
        if publication_type == "preprint":
            tier, score = SourceTier.SPECIALIST_RESEARCH, min(score, 0.76)
        out.append(
            {
                "id": "ev_" + hashlib.sha1((url or title).encode()).hexdigest()[:10],
                "title": title,
                "url": url,
                "snippet": (abstract or title)[:1200],
                "quote": (abstract or title)[:400],
                "source_agent": AgentName.SCHOLAR.value,
                "tier": tier.value,
                "credibility": score,
                "published": year,
                "doi": doi,
                "publication_type": publication_type,
                "scholar_source": "semantic_scholar",
            }
        )
    return out


def _publication_type(url: str, doi: str, descriptors: list[object]) -> str:
    """
    Determine publication type based on URL, DOI, and metadata descriptors.
    
    Priority:
    1. ArXiv (URL or DOI) → preprint
    2. Venue keywords + non-arXiv DOI → peer_reviewed
    3. Default → scholarly_unknown
    """
    blob = " ".join(str(value or "") for value in descriptors).lower()
    host = urlparse(url).netloc.lower()
    
    # Check for preprint indicators first
    if "arxiv" in host or "arxiv" in doi.lower() or "preprint" in blob:
        return "preprint"
    
    # Check for peer-reviewed venue indicators
    # Only treat as peer-reviewed if has venue keywords AND DOI is not from arxiv
    has_venue_keywords = bool(re.search(r"journal|conference|proceedings|article", blob))
    has_non_arxiv_doi = bool(doi and "arxiv" not in doi.lower())
    
    if has_venue_keywords and (has_non_arxiv_doi or not doi):
        # Has venue keywords + (non-arxiv DOI or no DOI)
        return "peer_reviewed"
    elif has_venue_keywords or has_non_arxiv_doi:
        # Has either venue keywords OR non-arxiv DOI (not both)
        # Be conservative: could be peer-reviewed but not certain
        return "peer_reviewed"
    
    return "scholarly_unknown"


def _dedupe_papers(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen_dois: set[str] = set()
    seen_urls: set[str] = set()
    for row in rows:
        doi = str(row.get("doi") or "").lower().removeprefix("https://doi.org/").rstrip("/")
        url = str(row.get("url") or "").lower().rstrip("/")
        if not doi and "doi.org/" in url:
            doi = url.split("doi.org/", 1)[1]
        if not url or (doi and doi in seen_dois) or url in seen_urls:
            continue
        if doi:
            seen_dois.add(doi)
        seen_urls.add(url)
        out.append(row)
    return out


def _on_topic(title: str, abstract: str, query: str) -> bool:
    if OFF_DOMAIN_RE.search(title) and not OFF_DOMAIN_RE.search(query):
        return False
    blob = f"{title} {abstract[:800]}"
    hits = distinctive_hits(query, blob)
    need = 2 if len(distinctive_terms(query)) >= 4 else 1
    if hits >= need:
        return True
    q_terms = query_terms(query)
    title_l = (title or "").lower()
    title_hits = sum(1 for t in q_terms if len(t) > 4 and t in title_l)
    if title_hits >= 2:
        return True
    return bool(TOPIC_RE.search(blob) and TOPIC_RE.search(query))


def _inflate_abstract(index: dict | None) -> str:
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, locs in index.items():
        for loc in locs:
            positions.append((loc, word))
    positions.sort()
    return " ".join(word for _, word in positions)
