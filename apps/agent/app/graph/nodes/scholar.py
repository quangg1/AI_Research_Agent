from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

import httpx

from app.domain.citations import is_citable_url
from app.domain.credibility import credibility_score
from app.domain.schema import AgentName, SourceTier
from app.graph.nodes.search import _questions
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event, logger
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


def scholar_node(state: ResearchState) -> dict:
    if "scholar" not in (state.get("agents_to_run") or []):
        return {"evidence": [], "traces": [{"node": "scholar", "skipped": True}]}
    if budget_from(state).remaining_calls <= 0:
        return {"evidence": [], "traces": [{"node": "scholar", "skipped": "budget"}]}

    questions = _questions(state, AgentName.SCHOLAR)
    hits: list[dict] = []
    external_calls = 0
    for question in questions[:3]:
        rows, calls = _scholar_search(question)
        hits.extend(rows[:8])
        external_calls += calls
    hits = _dedupe_papers(hits)
    event("scholar", n=len(hits))
    return {
        "evidence": hits,
        "traces": [{"node": "scholar", "n": len(hits), "external_calls": external_calls}],
    }


def _scholar_search(query: str) -> tuple[list[dict], int]:
    openalex = _openalex(query)
    calls = 1
    # Augment thin OpenAlex result sets without making a second call routinely.
    semantic: list[dict] = []
    if len(openalex) < 5:
        semantic = _semantic_scholar(query)
        calls += 1
    return _dedupe_papers(openalex + semantic), calls


def _openalex_query(query: str) -> str:
    cleaned = re.sub(r"[/?&#]+", " ", query or "")
    return re.sub(r"\s+", " ", cleaned).strip()[:180]


def _openalex(query: str) -> list[dict]:
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(
                "https://api.openalex.org/works",
                params={
                    "search": _openalex_query(query),
                    "per_page": 8,
                    "filter": "from_publication_date:2018-01-01",
                },
                headers={"User-Agent": "kiln-research-agent (mailto:research@kiln.local)"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("openalex_failed %s", exc)
        return []
    out = []
    for item in data.get("results", []):
        title = item.get("display_name") or "Untitled work"
        abstract = _inflate_abstract(item.get("abstract_inverted_index"))
        if not _on_topic(title, abstract, query):
            continue
        loc = item.get("primary_location") or {}
        source = loc.get("source") or {}
        doi = str(item.get("doi") or "").replace("https://doi.org/", "")
        url = (loc.get("landing_page_url") or "").strip() or (f"https://doi.org/{doi}" if doi else "") or (item.get("id") or "")
        if not is_citable_url(url):
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
    return out


def _semantic_scholar(query: str) -> list[dict]:
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": _openalex_query(query),
                    "limit": 8,
                    "fields": "title,abstract,url,year,externalIds,publicationTypes,venue",
                },
                headers={"User-Agent": "kiln-research-agent/0.1"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("semantic_scholar_failed %s", exc)
        return []
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
    blob = " ".join(str(value or "") for value in descriptors).lower()
    host = urlparse(url).netloc.lower()
    if "arxiv" in host or "arxiv" in doi.lower() or "preprint" in blob:
        return "preprint"
    if re.search(r"journal|conference|proceedings|article", blob) or doi:
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
