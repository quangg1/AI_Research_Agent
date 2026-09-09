from __future__ import annotations

import re
from typing import Any

from rank_bm25 import BM25Okapi

from app.retrieval.embed import cosine, embed_texts

STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "how", "does", "what",
    "when", "should", "we", "vs", "with", "from", "is", "are", "do", "can", "into",
    "than", "that", "this", "it", "as", "at", "be", "by", "not", "no", "you", "your",
}
FILLER = STOP | {
    "model", "models", "compare", "comparison", "using", "used", "based", "like", "via",
    "over", "same", "each", "also", "more", "most", "best", "good", "new", "first",
    "two", "use", "need", "make", "work", "works", "system", "systems", "data", "paper",
    "approach", "method", "methods", "result", "results", "performance", "large", "small",
    "open", "source", "code", "run", "running", "query", "question", "official",
    "documentation", "docs", "guide", "always", "require", "required", "instead",
}
TERM_RE = re.compile(r"[a-z0-9][a-z0-9.+_-]{2,}")


def query_terms(text: str) -> set[str]:
    return {t for t in TERM_RE.findall((text or "").lower()) if t not in STOP}


def distinctive_terms(text: str) -> set[str]:
    return {t for t in query_terms(text) if t not in FILLER}


def term_overlap(query: str, text: str) -> float:
    q = distinctive_terms(query) or query_terms(query)
    if not q:
        return 0.0
    return len(q & query_terms(text)) / len(q)


def distinctive_hits(query: str, text: str) -> int:
    q = distinctive_terms(query)
    if not q:
        return 0
    return len(q & query_terms(text))


def needed_hits(query: str) -> int:
    return 1


def corpus_is_relevant(query: str, documents: list[dict], min_overlap: float = 0.22) -> bool:
    if not documents:
        return False
    for doc in documents:
        blob = f"{doc.get('title', '')} {doc.get('snippet', '')}"
        hits = distinctive_hits(query, blob)
        overlap = term_overlap(query, blob)
        if hits >= 1 and overlap >= min_overlap:
            return True
        if overlap >= 0.45:
            return True
    return False


def hybrid_retrieve(
    query: str,
    documents: list[dict],
    k: int = 8,
    *,
    use_llm_reranker: bool = False,
) -> list[dict]:
    if not documents:
        return []
    scored: list[tuple[float, dict]] = []
    corpus = [f"{d.get('title', '')} {d.get('snippet', '')}" for d in documents]
    tokenized = [c.lower().split() for c in corpus]
    bm25 = BM25Okapi(tokenized)
    bm_scores = bm25.get_scores(query.lower().split())
    q_vec = embed_texts([query])[0]
    doc_vecs = embed_texts(corpus)
    dense = [cosine(q_vec, v) for v in doc_vecs]
    bm_norm = _minmax(list(bm_scores))
    dense_norm = _minmax(dense)
    for i, doc in enumerate(documents):
        blob = corpus[i]
        overlap = term_overlap(query, blob)
        hits = distinctive_hits(query, blob)
        if doc.get("source_kind") == "corpus_note" and (hits < 1 or overlap < 0.22):
            continue
        if hits < needed_hits(query) and float(bm_scores[i]) < 2.0:
            continue
        if overlap < 0.18 and float(bm_scores[i]) < 1.2:
            continue
        cred = float(doc.get("credibility") or 0.3)
        # Relevance-first: term overlap dominates; credibility is a light tie-break.
        score = float(0.35 * dense_norm[i] + 0.25 * bm_norm[i] + 0.35 * overlap + 0.05 * cred)
        scored.append((score, {**doc, "retrieval_score": round(score, 4), "term_overlap": round(overlap, 3)}))
    scored.sort(
        key=lambda x: (
            -float(x[1].get("term_overlap") or 0),
            -x[0],
            str(x[1].get("id") or x[1].get("url") or x[1].get("title") or ""),
        )
    )
    candidates = [doc for _, doc in scored[: max(k * 3, k)]]
    return rerank_candidates(query, candidates, k=k, use_llm=use_llm_reranker)


def rerank_candidates(query: str, candidates: list[dict], k: int = 8, *, use_llm: bool = False) -> list[dict]:
    """Rerank fused candidates for relevance and directness.

    A deterministic lexical/directness score always runs. Gemini can optionally
    score the whole batch in one call; malformed or failed responses simply
    preserve deterministic ordering.
    """
    if not candidates:
        return []
    rows: list[dict[str, Any]] = []
    for index, doc in enumerate(candidates):
        title = str(doc.get("title") or "")
        body = f"{title} {doc.get('snippet', '')}"
        overlap = term_overlap(query, body)
        title_overlap = term_overlap(query, title)
        retrieval = float(doc.get("retrieval_score") or 0)
        direct_markers = bool(re.search(r"\b(measured|benchmark|implements?|because|therefore|results?|we (?:show|find))\b", body, re.I))
        deterministic = 0.48 * retrieval + 0.30 * overlap + 0.17 * title_overlap + 0.05 * float(direct_markers)
        rows.append(
            {
                **doc,
                "rerank_score": round(deterministic, 4),
                "rerank_method": "deterministic",
                "rerank_external_calls": 0,
                "_stable_index": index,
            }
        )
    llm_scores = _gemini_rerank(query, rows) if use_llm else {}
    if llm_scores:
        for row in rows:
            row["rerank_external_calls"] = 1
            key = str(row.get("id") or row.get("url") or row["_stable_index"])
            relevance, directness = llm_scores.get(key, (None, None))
            if relevance is None:
                continue
            model_score = 0.65 * relevance + 0.35 * (directness or 0.0)
            row["rerank_score"] = round(0.45 * float(row["rerank_score"]) + 0.55 * model_score, 4)
            row["rerank_method"] = "gemini_batch"
            row["rerank_external_calls"] = 1
    rows.sort(key=lambda row: (-float(row["rerank_score"]), int(row["_stable_index"])))
    for row in rows:
        row.pop("_stable_index", None)
    return rows[:k]


def _gemini_rerank(query: str, candidates: list[dict]) -> dict[str, tuple[float, float]]:
    try:
        from app.llm.client import CreditsExhaustedError, llm

        if not llm.available:
            return {}
        compact = [
            {
                "id": str(row.get("id") or row.get("url") or i),
                "title": str(row.get("title") or "")[:240],
                "text": str(row.get("snippet") or "")[:700],
            }
            for i, row in enumerate(candidates[:30])
        ]
        payload = llm.generate_json(
            prompt=(
                f"Research question: {query[:600]}\nCandidates: {compact}\n"
                "Score every candidate from 0 to 1 for relevance to the exact question and "
                "directness (whether it directly supports an answer rather than sharing vocabulary). "
                'Return {"scores":[{"id":"...","relevance":0.0,"directness":0.0}]}.'
            ),
            system="You are a conservative evidence reranker. Return JSON only.",
            max_tokens=1800,
        )
        scores = payload.get("scores") if isinstance(payload, dict) else None
        if not isinstance(scores, list):
            return {}
        out: dict[str, tuple[float, float]] = {}
        for item in scores:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or "")
            if not key:
                continue
            relevance = max(0.0, min(1.0, float(item.get("relevance") or 0)))
            directness = max(0.0, min(1.0, float(item.get("directness") or 0)))
            out[key] = (relevance, directness)
        return out
    except CreditsExhaustedError:
        raise
    except Exception:
        return {}


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    nums = [float(v) for v in values]
    lo, hi = min(nums), max(nums)
    if hi - lo < 1e-9:
        return [0.5] * len(nums)
    return [(v - lo) / (hi - lo) for v in nums]
