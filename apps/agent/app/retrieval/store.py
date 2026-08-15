from __future__ import annotations

import hashlib
import os
import uuid
from collections import defaultdict
from urllib.parse import urlparse

from app.config import settings
from app.observability.logging import logger
from app.persistence.postgres import transaction
from app.retrieval.chunk import load_corpus
from app.retrieval.embed import DIM, embed_texts
from app.retrieval.hybrid import hybrid_retrieve

_MEMORY: list[dict] = []
_POINT_NAMESPACE = uuid.UUID("8939b197-8c31-49d7-a526-f4971e493a65")


def _memory_backend() -> bool:
    return (
        os.getenv("KNOWLEDGE_BACKEND", settings.knowledge_backend).strip().lower()
        == "memory"
    )


def _embedding_model() -> str:
    return (
        f"gemini:{settings.gemini_embed_model}"
        if settings.google_api_key
        else f"hash:{DIM}"
    )


def _doc_from_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "url": row.get("url") or "",
        "host": row.get("host") or "",
        "snippet": row.get("snippet") or "",
        "quote": row.get("quote") or "",
        "tier": row.get("tier") or "unknown",
        "credibility": float(row.get("credibility") or 0),
        "published": row.get("published") or "",
        "path": row.get("source_path") or "",
        "source_kind": row.get("source_kind") or "corpus_note",
        "source_agent": "docs",
        "generation": int(row.get("generation") or 1),
        "embedding_model": row.get("embedding_model") or "",
    }


def get_store_documents() -> list[dict]:
    global _MEMORY
    if _memory_backend():
        if not _MEMORY:
            _MEMORY = load_corpus()
        return [dict(doc) for doc in _MEMORY]
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT id, url, title, host, tier, snippet, source_path, quote,
                   credibility, published, source_kind, generation, embedding_model
            FROM corpus_documents
            WHERE active = TRUE
            ORDER BY source_path, chunk_index, id
            """
        ).fetchall()
    return [_doc_from_row(row) for row in rows]


def _prepare_documents() -> list[dict]:
    counters: dict[str, int] = defaultdict(int)
    prepared: list[dict] = []
    for raw in load_corpus():
        doc = dict(raw)
        source_path = str(doc.get("path") or "")
        chunk_index = counters[source_path]
        counters[source_path] += 1
        snippet = str(doc.get("snippet") or "")
        doc["source_path"] = source_path
        doc["chunk_index"] = chunk_index
        doc["content_hash"] = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
        doc["host"] = urlparse(str(doc.get("url") or "")).hostname or "corpus"
        prepared.append(doc)
    return prepared


def ingest_corpus() -> int:
    global _MEMORY
    docs = _prepare_documents()
    if _memory_backend():
        _MEMORY = docs
        return len(docs)

    model = _embedding_model()
    sync_id = uuid.uuid4()
    source_hash = hashlib.sha256(
        "".join(sorted(str(doc["content_hash"]) for doc in docs)).encode("utf-8")
    ).hexdigest()
    with transaction() as conn:
        previous_models = {
            row["embedding_model"]
            for row in conn.execute(
                """
                SELECT DISTINCT embedding_model FROM corpus_documents
                WHERE active = TRUE AND embedding_model IS NOT NULL
                """
            ).fetchall()
        }
        generation = int(
            conn.execute(
                "SELECT COALESCE(MAX(generation), 0) + 1 AS value FROM corpus_documents"
            ).fetchone()["value"]
        )
        conn.execute(
            """
            INSERT INTO corpus_sync_runs
                (id, status, generation, source_hash, documents_seen, embedding_model)
            VALUES (%s, 'running', %s, %s, %s, %s)
            """,
            (sync_id, generation, source_hash, len(docs), model),
        )
        conn.execute(
            "UPDATE corpus_documents SET active = FALSE, updated_at = NOW() WHERE active = TRUE"
        )
        for doc in docs:
            conn.execute(
                """
                INSERT INTO corpus_documents
                    (id, url, title, host, tier, snippet, source_path, chunk_index,
                     content_hash, quote, credibility, published, source_kind,
                     generation, active, embedding_model, indexed_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, TRUE, %s, NULL, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    url = EXCLUDED.url, title = EXCLUDED.title, host = EXCLUDED.host,
                    tier = EXCLUDED.tier, snippet = EXCLUDED.snippet,
                    source_path = EXCLUDED.source_path, chunk_index = EXCLUDED.chunk_index,
                    content_hash = EXCLUDED.content_hash, quote = EXCLUDED.quote,
                    credibility = EXCLUDED.credibility, published = EXCLUDED.published,
                    source_kind = EXCLUDED.source_kind, generation = EXCLUDED.generation,
                    active = TRUE, embedding_model = EXCLUDED.embedding_model,
                    indexed_at = NULL, updated_at = NOW()
                """,
                (
                    doc["id"],
                    doc.get("url"),
                    doc.get("title"),
                    doc.get("host"),
                    doc.get("tier"),
                    doc.get("snippet"),
                    doc.get("source_path"),
                    doc.get("chunk_index"),
                    doc.get("content_hash"),
                    doc.get("quote"),
                    doc.get("credibility"),
                    doc.get("published"),
                    doc.get("source_kind"),
                    generation,
                    model,
                ),
            )

    try:
        _index_qdrant(
            docs,
            generation,
            model,
            force_rebuild=bool(previous_models and previous_models != {model}),
        )
        with transaction() as conn:
            conn.execute(
                """
                UPDATE corpus_documents SET indexed_at = NOW(), updated_at = NOW()
                WHERE active = TRUE AND generation = %s AND embedding_model = %s
                """,
                (generation, model),
            )
            conn.execute(
                """
                UPDATE corpus_sync_runs
                SET status = 'completed', documents_indexed = %s, completed_at = NOW()
                WHERE id = %s
                """,
                (len(docs), sync_id),
            )
        logger.info("qdrant_ingested %s generation=%s", len(docs), generation)
    except Exception as exc:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE corpus_sync_runs SET status = 'failed', error = %s, completed_at = NOW()
                WHERE id = %s
                """,
                (str(exc)[:2000], sync_id),
            )
        logger.warning("qdrant_unavailable using_postgres %s", exc)
    return len(docs)


def _index_qdrant(
    docs: list[dict],
    generation: int,
    model: str,
    *,
    force_rebuild: bool = False,
) -> None:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    client = QdrantClient(url=settings.qdrant_url, timeout=30)
    vectors = (
        embed_texts([str(doc.get("snippet") or "") for doc in docs]) if docs else []
    )
    dim = len(vectors[0]) if vectors else DIM
    collections = {item.name for item in client.get_collections().collections}
    rebuild = force_rebuild or settings.qdrant_collection not in collections
    if not rebuild:
        info = client.get_collection(settings.qdrant_collection)
        vector_config = info.config.params.vectors
        size = getattr(vector_config, "size", None)
        rebuild = size != dim
    if rebuild and settings.qdrant_collection in collections:
        client.delete_collection(settings.qdrant_collection)
    if rebuild:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )

    point_ids = [str(uuid.uuid5(_POINT_NAMESPACE, str(doc["id"]))) for doc in docs]
    if docs:
        client.upsert(
            collection_name=settings.qdrant_collection,
            wait=True,
            points=[
                qm.PointStruct(
                    id=point_ids[index],
                    vector=vectors[index],
                    payload={
                        "document_id": doc["id"],
                        "generation": generation,
                        "embedding_model": model,
                    },
                )
                for index, doc in enumerate(docs)
            ],
        )
    stale = _qdrant_point_ids(client) - set(point_ids)
    if stale:
        client.delete(
            collection_name=settings.qdrant_collection,
            wait=True,
            points_selector=qm.PointIdsList(points=sorted(stale)),
        )


def _qdrant_point_ids(client) -> set[str]:
    ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=settings.qdrant_collection,
            limit=256,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        ids.update(str(point.id) for point in points)
        if offset is None:
            return ids


def corpus_available() -> bool:
    return len(get_store_documents()) > 0


def corpus_stats() -> dict:
    docs = get_store_documents()
    hosts: dict[str, int] = {}
    tiers: dict[str, int] = {}
    for d in docs:
        url = d.get("url") or ""
        host = url.split("/")[2] if url.startswith("http") else "corpus"
        hosts[host] = hosts.get(host, 0) + 1
        tier = d.get("tier") or "unknown"
        tiers[tier] = tiers.get(tier, 0) + 1
    return {
        "documents": len(docs),
        "hosts": hosts,
        "tiers": tiers,
        "items": [
            {
                "id": d.get("id"),
                "title": d.get("title"),
                "url": d.get("url"),
                "tier": d.get("tier"),
                "credibility": d.get("credibility"),
                "published": d.get("published"),
                "snippet": (d.get("snippet") or "")[:220],
            }
            for d in docs
        ],
    }


def search_qdrant(query: str, k: int = 8) -> list[dict] | None:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm

        client = QdrantClient(url=settings.qdrant_url, timeout=5)
        vector = embed_texts([query])[0]
        with transaction() as conn:
            current = conn.execute(
                """
                SELECT generation, embedding_model FROM corpus_documents
                WHERE active = TRUE ORDER BY generation DESC LIMIT 1
                """
            ).fetchone()
        if not current:
            return []
        hits = client.search(
            collection_name=settings.qdrant_collection,
            query_vector=vector,
            query_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="generation",
                        match=qm.MatchValue(value=current["generation"]),
                    ),
                    qm.FieldCondition(
                        key="embedding_model",
                        match=qm.MatchValue(value=current["embedding_model"]),
                    ),
                ]
            ),
            limit=k,
        )
        document_ids = [hit.payload.get("document_id") for hit in hits if hit.payload]
        if not document_ids:
            return []
        with transaction() as conn:
            rows = conn.execute(
                """
                SELECT id, url, title, host, tier, snippet, source_path, quote,
                       credibility, published, source_kind, generation, embedding_model
                FROM corpus_documents
                WHERE active = TRUE AND id = ANY(%s)
                """,
                (document_ids,),
            ).fetchall()
        by_id = {row["id"]: _doc_from_row(row) for row in rows}
        return [by_id[doc_id] for doc_id in document_ids if doc_id in by_id]
    except Exception as exc:
        logger.warning("qdrant_search_failed using_postgres %s", exc)
        return hybrid_retrieve(query, get_store_documents(), k=k)
