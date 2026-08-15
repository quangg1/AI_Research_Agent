from __future__ import annotations

from app.domain.schema import AgentName
from app.graph.nodes.search import _questions
from app.graph.state import ResearchState, budget_from
from app.observability.logging import event
from app.retrieval.chunk import load_corpus
from app.retrieval.hybrid import corpus_is_relevant, hybrid_retrieve
from app.retrieval.store import get_store_documents, ingest_corpus, search_qdrant


def docs_node(state: ResearchState) -> dict:
    if "docs" not in (state.get("agents_to_run") or []):
        return {"evidence": [], "traces": [{"node": "docs", "skipped": True}]}
    if budget_from(state).remaining_calls <= 0:
        return {"evidence": [], "traces": [{"node": "docs", "skipped": "budget"}]}

    docs = get_store_documents()
    if not docs:
        ingest_corpus()
        docs = get_store_documents() or load_corpus()
    if not docs:
        event("docs", n=0, skipped="no_corpus")
        return {"evidence": [], "traces": [{"node": "docs", "skipped": "no_corpus"}]}
    if not corpus_is_relevant(state["query"], docs):
        event("docs", n=0, skipped="off_corpus")
        return {"evidence": [], "traces": [{"node": "docs", "skipped": "off_corpus"}]}
    questions = _questions(state, AgentName.DOCS)
    hits: list[dict] = []
    for question in questions[:2]:
        qdrant_hits = search_qdrant(question, k=6)
        if qdrant_hits:
            hits.extend(qdrant_hits)
        hits.extend(hybrid_retrieve(question, docs, k=6))
    # de-dupe
    seen: set[str] = set()
    unique = []
    for h in hits:
        hid = h.get("id")
        if not hid or hid in seen:
            continue
        seen.add(hid)
        unique.append(h)
    event("docs", n=len(unique))
    return {"evidence": unique, "traces": [{"node": "docs", "n": len(unique)}]}
