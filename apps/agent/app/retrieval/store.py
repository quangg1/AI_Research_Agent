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
GLOBAL_SCOPE = "__global__"


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


def _org_scope(org_id: str | None) -> str:
    return org_id or GLOBAL_SCOPE


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
        "org_id": row.get("org_id"),
    }


def get_store_documents(org_id: str | None = None) -> list[dict]:
    global _MEMORY
    if _memory_backend():
        if not _MEMORY:
            _MEMORY = _prepare_documents(None)
        docs = [dict(doc) for doc in _MEMORY]
        if org_id:
            return [d for d in docs if not d.get("org_id") or d.get("org_id") == org_id]
        return docs
    params: list[str | None] = []
    org_clause = ""
    if org_id:
        org_clause = "AND (org_id IS NULL OR org_id = %s)"
        params.append(org_id)
    with transaction() as conn:
        rows = conn.execute(
            f"""
            SELECT id, url, title, host, tier, snippet, source_path, quote,
                   credibility, published, source_kind, generation, embedding_model, org_id
            FROM corpus_documents
            WHERE active = TRUE {org_clause}
            ORDER BY source_path, chunk_index, id
            """,
            tuple(params),
        ).fetchall()
    return [_doc_from_row(row) for row in rows]


def _prepare_documents(org_id: str | None) -> list[dict]:
    counters: dict[str, int] = defaultdict(int)
    prepared: list[dict] = []
    raw_docs = load_corpus(org_id=org_id) if org_id else load_corpus()
    for raw in raw_docs:
        doc = dict(raw)
        source_path = str(doc.get("path") or "")
        chunk_index = counters[source_path]
        counters[source_path] += 1
        snippet = str(doc.get("snippet") or "")
        doc["source_path"] = source_path
        doc["chunk_index"] = chunk_index
        doc["content_hash"] = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
        doc["host"] = urlparse(str(doc.get("url") or "")).hostname or "corpus"
        doc["org_id"] = org_id
        prepared.append(doc)
    return prepared


def ingest_corpus(org_id: str | None = None) -> int:
    global _MEMORY
    docs = _prepare_documents(org_id)
    if _memory_backend():
        if org_id:
            _MEMORY = [d for d in _MEMORY if d.get("org_id") != org_id] + docs
        else:
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
                  AND org_id IS NOT DISTINCT FROM %s
                """,
                (org_id,),
            ).fetchall()
        }
        generation = int(
            conn.execute(
                """
                SELECT COALESCE(MAX(generation), 0) + 1 AS value
                FROM corpus_documents
                WHERE org_id IS NOT DISTINCT FROM %s
                """,
                (org_id,),
            ).fetchone()["value"]
        )
        conn.execute(
            """
            INSERT INTO corpus_sync_runs
                (id, status, generation, source_hash, documents_seen, embedding_model, org_id)
            VALUES (%s, 'running', %s, %s, %s, %s, %s)
            """,
            (sync_id, generation, source_hash, len(docs), model, org_id),
        )
        conn.execute(
            """
            UPDATE corpus_documents
            SET active = FALSE, updated_at = NOW()
            WHERE active = TRUE AND org_id IS NOT DISTINCT FROM %s
            """,
            (org_id,),
        )
        for doc in docs:
            conn.execute(
                """
                INSERT INTO corpus_documents
                    (id, url, title, host, tier, snippet, source_path, chunk_index,
                     content_hash, quote, credibility, published, source_kind,
                     generation, active, embedding_model, indexed_at, updated_at, org_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, TRUE, %s, NULL, NOW(), %s)
                ON CONFLICT (id) DO UPDATE SET
                    url = EXCLUDED.url, title = EXCLUDED.title, host = EXCLUDED.host,
                    tier = EXCLUDED.tier, snippet = EXCLUDED.snippet,
                    source_path = EXCLUDED.source_path, chunk_index = EXCLUDED.chunk_index,
                    content_hash = EXCLUDED.content_hash, quote = EXCLUDED.quote,
                    credibility = EXCLUDED.credibility, published = EXCLUDED.published,
                    source_kind = EXCLUDED.source_kind, generation = EXCLUDED.generation,
                    active = TRUE, embedding_model = EXCLUDED.embedding_model,
                    indexed_at = NULL, updated_at = NOW(), org_id = EXCLUDED.org_id
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
                    org_id,
                ),
            )

    try:
        if settings.qdrant_enabled() and docs:
            _index_qdrant(
                docs,
                generation,
                model,
                org_id,
                force_rebuild=bool(previous_models and previous_models != {model}),
            )
        with transaction() as conn:
            conn.execute(
                """
                UPDATE corpus_documents SET indexed_at = NOW(), updated_at = NOW()
                WHERE active = TRUE AND generation = %s AND embedding_model = %s
                  AND org_id IS NOT DISTINCT FROM %s
                """,
                (generation, model, org_id),
            )
            conn.execute(
                """
                UPDATE corpus_sync_runs
                SET status = 'completed', documents_indexed = %s, completed_at = NOW()
                WHERE id = %s
                """,
                (len(docs), sync_id),
            )
        logger.info(
            "qdrant_ingested org=%s count=%s generation=%s",
            org_id or GLOBAL_SCOPE,
            len(docs),
            generation,
        )
    except Exception as exc:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE corpus_sync_runs SET status = 'failed', error = %s, completed_at = NOW()
                WHERE id = %s
                """,
                (str(exc)[:2000], sync_id),
            )
        logger.warning("qdrant_unavailable using_postgres org=%s %s", org_id, exc)
    return len(docs)


def _index_qdrant(
    docs: list[dict],
    generation: int,
    model: str,
    org_id: str | None,
    *,
    force_rebuild: bool = False,
) -> None:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    org_scope = _org_scope(org_id)
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
                        "org_scope": org_scope,
                    },
                )
                for index, doc in enumerate(docs)
            ],
        )
    stale = _qdrant_point_ids(client, org_scope) - set(point_ids)
    if stale:
        client.delete(
            collection_name=settings.qdrant_collection,
            wait=True,
            points_selector=qm.PointIdsList(points=sorted(stale)),
        )


def _qdrant_point_ids(client, org_scope: str) -> set[str]:
    from qdrant_client.http import models as qm

    ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="org_scope",
                        match=qm.MatchValue(value=org_scope),
                    )
                ]
            ),
            limit=256,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        ids.update(str(point.id) for point in points)
        if offset is None:
            return ids


def _latest_generation(org_id: str | None) -> dict | None:
    with transaction() as conn:
        return conn.execute(
            """
            SELECT generation, embedding_model FROM corpus_documents
            WHERE active = TRUE AND org_id IS NOT DISTINCT FROM %s
            ORDER BY generation DESC LIMIT 1
            """,
            (org_id,),
        ).fetchone()


def corpus_available(org_id: str | None = None) -> bool:
    return len(get_store_documents(org_id)) > 0


def corpus_stats(org_id: str | None = None) -> dict:
    docs = get_store_documents(org_id)
    hosts: dict[str, int] = {}
    tiers: dict[str, int] = {}
    org_docs = 0
    global_docs = 0
    for d in docs:
        if d.get("org_id"):
            org_docs += 1
        else:
            global_docs += 1
        url = d.get("url") or ""
        host = url.split("/")[2] if url.startswith("http") else "corpus"
        hosts[host] = hosts.get(host, 0) + 1
        tier = d.get("tier") or "unknown"
        tiers[tier] = tiers.get(tier, 0) + 1
    return {
        "documents": len(docs),
        "org_documents": org_docs,
        "global_documents": global_docs,
        "org_id": org_id,
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
                "org_id": d.get("org_id"),
                "source_kind": d.get("source_kind"),
            }
            for d in docs
        ],
    }


def search_qdrant(query: str, k: int = 8, org_id: str | None = None) -> list[dict] | None:
    if not settings.qdrant_enabled():
        return None
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm

        client = QdrantClient(url=settings.qdrant_url, timeout=5)
        vector = embed_texts([query])[0]
        scopes: list[qm.Filter] = []
        global_gen = _latest_generation(None)
        if global_gen:
            scopes.append(
                qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="org_scope",
                            match=qm.MatchValue(value=GLOBAL_SCOPE),
                        ),
                        qm.FieldCondition(
                            key="generation",
                            match=qm.MatchValue(value=global_gen["generation"]),
                        ),
                        qm.FieldCondition(
                            key="embedding_model",
                            match=qm.MatchValue(value=global_gen["embedding_model"]),
                        ),
                    ]
                )
            )
        if org_id:
            org_gen = _latest_generation(org_id)
            if org_gen:
                scopes.append(
                    qm.Filter(
                        must=[
                            qm.FieldCondition(
                                key="org_scope",
                                match=qm.MatchValue(value=org_id),
                            ),
                            qm.FieldCondition(
                                key="generation",
                                match=qm.MatchValue(value=org_gen["generation"]),
                            ),
                            qm.FieldCondition(
                                key="embedding_model",
                                match=qm.MatchValue(value=org_gen["embedding_model"]),
                            ),
                        ]
                    )
                )
        if not scopes:
            return []
        hits = client.search(
            collection_name=settings.qdrant_collection,
            query_vector=vector,
            query_filter=qm.Filter(should=scopes),
            limit=k,
        )
        document_ids = [hit.payload.get("document_id") for hit in hits if hit.payload]
        if not document_ids:
            return []
        params: list[str | None] = [document_ids]
        org_clause = ""
        if org_id:
            org_clause = "AND (org_id IS NULL OR org_id = %s)"
            params.append(org_id)
        with transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT id, url, title, host, tier, snippet, source_path, quote,
                       credibility, published, source_kind, generation, embedding_model, org_id
                FROM corpus_documents
                WHERE active = TRUE AND id = ANY(%s) {org_clause}
                """,
                tuple(params),
            ).fetchall()
        by_id = {row["id"]: _doc_from_row(row) for row in rows}
        return [by_id[doc_id] for doc_id in document_ids if doc_id in by_id]
    except Exception as exc:
        logger.warning("qdrant_search_failed using_postgres org=%s %s", org_id, exc)
        return hybrid_retrieve(query, get_store_documents(org_id), k=k)
