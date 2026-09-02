from __future__ import annotations

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import httpx

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

    questions = _questions(state, AgentName.SCHOLAR)
    parallel = fanout_parallelism(state, ceiling=FANOUT_CEILING)
    hits: list[dict] = []
    external_calls = 0
    trace_entries: list[dict] = []

    with trace_span("scholar", active_agent="scholar", parallel=parallel) as span:
        def run_one(question: str) -> tuple[str, list[dict], int]:
            rows, calls = _scholar_search(question)
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


def _scholar_search(query: str) -> tuple[list[dict], int]:
    global _s2_is_rate_limited, _s2_rate_limit_until
    
    q = compact_retrieval_query(query, agent="scholar")
    openalex = _openalex(q)
    calls = 1
    
    # Check if we're in a rate-limit cooldown period
    now = time.time()
    if _s2_is_rate_limited and now < _s2_rate_limit_until:
        cooldown_remaining = int(_s2_rate_limit_until - now)
        logger.info(f"semantic_scholar_skipped: in rate-limit cooldown for {cooldown_remaining}s, using OpenAlex only")
        return openalex, calls
    
    # TEMPORARY: Skip Semantic Scholar entirely while API key activation is pending
    # OpenAlex provides sufficient coverage (8 results typically) without rate limits
    # TODO: Re-enable S2 once API key is confirmed active (24-48 hours)
    # Original condition: if len(openalex) < 5
    logger.info(f"semantic_scholar_skipped: temporarily disabled, using OpenAlex only (got {len(openalex)} results)")
    return openalex, calls
    
    # Augment thin OpenAlex result sets without making a second call routinely.
    # semantic: list[dict] = []
    # if len(openalex) < 5:
    #     semantic = _semantic_scholar(q)
    #     calls += 1
    # return _dedupe_papers(openalex + semantic), calls


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
        url = (loc.get("landing_page_url") or "").strip() or (f"https://doi.org/{doi}" if doi else "") or (item.get("id") or "")
        if not is_citable_url(url):
            filtered_out_by_url += 1
            continue
        year = str(item.get("publication_year") or "")
        eid = "ev_" + hashlib.sha1((url or title).encode()).hexdigest()[:10]
        publication_type = _publication_type(
            url,
            doi,
            [source.get("type"), source.get("display_name"), item.get("type"), item.get("type_crossref")],
        )
        fallback = SourceTier.PEER_REVIEWED if publication_type == "peer_reviewed" else SourceTier.SPECIALIST_RESEARCH
        tier, score = credibility_score(url, year, fallback)
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
                        "fields": "title,abstract,url,year,externalIds,publicationTypes,venue",
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
        url = (f"https://doi.org/{doi}" if doi else "") or (f"https://arxiv.org/abs/{arxiv}" if arxiv else "") or str(item.get("url") or "")
        if not is_citable_url(url):
            continue
        year = str(item.get("year") or "")
        publication_type = _publication_type(url, doi, list(item.get("publicationTypes") or []) + [item.get("venue")])
        fallback = SourceTier.PEER_REVIEWED if publication_type == "peer_reviewed" else SourceTier.SPECIALIST_RESEARCH
        tier, score = credibility_score(url, year, fallback)
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
