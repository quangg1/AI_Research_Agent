"""Durable answer reuse backed by Postgres."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from app.config import settings
from app.domain.textutil import jaccard, query_fingerprint, user_goal
from app.observability.logging import logger
from app.persistence.postgres import transaction
from app.retrieval.embed import DIM, cosine, embed_texts

MAX_BODY_CHARS = 80_000
MAX_CITATIONS = 28
_MEMORY: list[dict[str, Any]] = []
_STORE_LOCK = threading.RLock()


@dataclass
class KnowledgeHit:
    record: dict[str, Any]
    similarity: float
    mode: str
    age_days: float

    @property
    def is_cached(self) -> bool:
        return self.mode == "cached"


def _backend() -> str:
    return os.getenv("KNOWLEDGE_BACKEND", settings.knowledge_backend).strip().lower()


def _embedding_model() -> str:
    return (
        f"gemini:{settings.gemini_embed_model}"
        if settings.google_api_key
        else f"hash:{DIM}"
    )


def _embed(goal: str) -> list[float]:
    try:
        return embed_texts([goal])[0]
    except Exception as exc:
        logger.debug("knowledge_embed_failed %s", exc)
        return []


def reset_cache() -> None:
    with _STORE_LOCK:
        _MEMORY.clear()


def _timestamp(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    answer = dict(row.get("answer") or {})
    answer.update(
        {
            "id": str(row["id"]),
            "goal": row.get("goal") or answer.get("goal") or "",
            "citations": list(row.get("citations") or []),
            "embedding": list(row.get("embedding") or []),
            "embedding_model": row.get("embedding_model") or "",
            "fingerprint": list(row.get("fingerprint") or []),
            "queries": list(row.get("queries") or []),
            "depth_score": int(row.get("depth_score") or 0),
            "reuse_count": int(row.get("reuse_count") or 0),
            "version": int(row.get("version") or 1),
            "status": row.get("status")
            or ("active" if row.get("active", True) else "superseded"),
            "active": bool(row.get("active", True)),
            "last_reused_at": _timestamp(row.get("last_reused_at")),
            "created_at": _timestamp(row.get("created_at") or answer.get("created_at")),
            "updated_at": _timestamp(row.get("updated_at") or answer.get("updated_at")),
        }
    )
    return answer


def load_records(org_id: str | None = None) -> list[dict[str, Any]]:
    if _backend() == "memory":
        with _STORE_LOCK:
            rows = [
                dict(row) for row in _MEMORY if row.get("status", "active") == "active"
            ]
            if org_id:
                rows = [row for row in rows if row.get("org_id") == org_id]
            return rows
    try:
        with transaction() as conn:
            if org_id:
                rows = conn.execute(
                    """
                    SELECT id, goal, answer, citations, embedding, embedding_model,
                           fingerprint, queries, depth_score, reuse_count, status,
                           version, active, last_reused_at, created_at, updated_at,
                           org_id, created_by
                    FROM knowledge_records
                    WHERE active = TRUE AND status = 'active' AND org_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (org_id, int(settings.knowledge_max_records)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, goal, answer, citations, embedding, embedding_model,
                           fingerprint, queries, depth_score, reuse_count, status,
                           version, active, last_reused_at, created_at, updated_at,
                           org_id, created_by
                    FROM knowledge_records
                    WHERE active = TRUE AND status = 'active'
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (int(settings.knowledge_max_records),),
                ).fetchall()
        return [_row_to_record(row) for row in rows]
    except Exception:
        if settings.persistence_required:
            raise
        logger.exception("knowledge_load_failed")
        return []


FRESHNESS_DAYS_BY_TYPE = {
    "factual": 21,
    "comparison": 14,
    "open_research": 7,
}


def _fresh_days_for(record: dict[str, Any]) -> float:
    qtype = str(record.get("query_type") or record.get("answer", {}).get("query_type") or "factual")
    return float(FRESHNESS_DAYS_BY_TYPE.get(qtype, settings.knowledge_fresh_days))


def partial_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Seed augment runs with high-confidence prior claims plus citations."""
    out = seed_evidence(record)
    seen = {e.get("url") for e in out}
    for claim in record.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        if float(claim.get("confidence") or 0) < 0.65:
            continue
        url = claim.get("url") or ""
        if url and url in seen:
            continue
        out.append(
            {
                "id": f"kclaim-{claim.get('slot_id') or len(out)}",
                "title": (claim.get("text") or "Prior claim")[:200],
                "url": url,
                "snippet": (claim.get("quote") or claim.get("text") or "")[:800],
                "quote": (claim.get("quote") or "")[:400],
                "tier": claim.get("tier") or "unknown",
                "credibility": float(claim.get("confidence") or 0.65),
                "source_agent": "search",
                "source_kind": "knowledge_partial",
                "reused_from": record.get("id"),
            }
        )
        if url:
            seen.add(url)
    return out[:16]


def lookup(query: str, org_id: str | None = None) -> KnowledgeHit | None:
    if not settings.knowledge_enabled:
        return None
    goal = user_goal(query) or (query or "")
    if not goal.strip():
        return None
    fingerprint = query_fingerprint(goal)
    vector = _embed(goal)
    model = _embedding_model()
    best: tuple[float, float, dict[str, Any]] | None = None
    for row in load_records(org_id):
        stored = row.get("embedding") or []
        same_model = (row.get("embedding_model") or "") == model
        sim = cosine(vector, stored) if vector and stored and same_model else 0.0
        overlap = jaccard(fingerprint, set(row.get("fingerprint") or []))
        score = max(sim, overlap)
        if best is None or score > best[0]:
            best = (score, overlap, row)
    if best is None:
        return None
    score, overlap, row = best
    age_days = max(0.0, (time.time() - _timestamp(row.get("updated_at"))) / 86400.0)
    if score >= float(settings.knowledge_reuse_similarity) and overlap >= 0.5:
        fresh_days = _fresh_days_for(row)
        mode = "cached" if age_days <= fresh_days else "augment"
        return KnowledgeHit(row, round(score, 4), mode, round(age_days, 2))
    if score >= float(settings.knowledge_augment_similarity):
        return KnowledgeHit(row, round(score, 4), "augment", round(age_days, 2))
    return None


def seed_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, citation in enumerate(record.get("citations") or []):
        if not isinstance(citation, dict):
            continue
        url = citation.get("url") or ""
        title = citation.get("title") or url
        if not (url or title):
            continue
        quote = citation.get("quote") or ""
        out.append(
            {
                "id": citation.get("evidence_id")
                or f"know-{str(record.get('id', 'r'))[:8]}-{i}",
                "title": title,
                "url": url,
                "snippet": quote or title,
                "quote": quote,
                "tier": citation.get("tier") or "unknown",
                "credibility": float(citation.get("credibility") or 0.5),
                "published": citation.get("published") or "",
                "source_agent": "search",
                "source_kind": "knowledge_reuse",
                "reused_from": record.get("id"),
            }
        )
    return out


def save_answer(
    query: str,
    report: dict[str, Any],
    coverage: dict[str, Any] | None = None,
    prior_id: str | None = None,
    *,
    org_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    if not settings.knowledge_enabled:
        return None
    goal = user_goal(query) or (query or "")
    if not goal.strip() or not isinstance(report, dict):
        return None
    if not (
        report.get("body_markdown") or report.get("executive_summary") or ""
    ).strip():
        return None
    fresh = _record_from_report(goal, report, coverage)
    if org_id:
        fresh["org_id"] = org_id
    if user_id:
        fresh["created_by"] = user_id
    if _backend() == "memory":
        return _memory_save(fresh, prior_id)
    try:
        with transaction() as conn:
            prior = None
            if prior_id:
                row = conn.execute(
                    """
                    SELECT id, goal, answer, citations, embedding, embedding_model,
                           fingerprint, queries, depth_score, reuse_count, status,
                           version, active, last_reused_at, created_at, updated_at
                    FROM knowledge_records WHERE id = %s AND active = TRUE
                    FOR UPDATE
                    """,
                    (prior_id,),
                ).fetchone()
                prior = _row_to_record(row) if row else None
            record = _merge(prior, fresh) if prior else fresh
            if prior:
                conn.execute(
                    """
                    UPDATE knowledge_records
                    SET active = FALSE, status = 'superseded', updated_at = NOW()
                    WHERE id = %s
                    """,
                    (prior_id,),
                )
            _insert(conn, record, org_id=org_id, user_id=user_id)
            return record
    except Exception:
        if settings.persistence_required:
            raise
        logger.exception("knowledge_save_failed")
        return None


def _memory_save(fresh: dict[str, Any], prior_id: str | None) -> dict[str, Any]:
    with _STORE_LOCK:
        prior = next(
            (
                r
                for r in _MEMORY
                if r.get("id") == prior_id and r.get("status") == "active"
            ),
            None,
        )
        record = _merge(prior, fresh) if prior else fresh
        if prior:
            prior["status"] = "superseded"
        _MEMORY.append(record)
        return dict(record)


def _insert(
    conn,
    record: dict[str, Any],
    *,
    org_id: str | None = None,
    user_id: str | None = None,
) -> None:
    answer = {
        key: value
        for key, value in record.items()
        if key
        not in {
            "id",
            "goal",
            "citations",
            "embedding",
            "embedding_model",
            "fingerprint",
            "queries",
            "depth_score",
            "reuse_count",
            "version",
            "status",
            "active",
            "last_reused_at",
            "created_at",
            "updated_at",
        }
    }
    conn.execute(
        """
        INSERT INTO knowledge_records
            (id, goal, answer, citations, embedding, embedding_model, fingerprint,
             queries, depth_score, reuse_count, status, version, active,
             last_reused_at, created_at, updated_at, org_id, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE,
                CASE WHEN %s > 0 THEN to_timestamp(%s) ELSE NULL END,
                to_timestamp(%s), to_timestamp(%s), %s, %s)
        """,
        (
            record["id"],
            record["goal"],
            Jsonb(answer),
            Jsonb(record.get("citations") or []),
            Jsonb(record.get("embedding") or []),
            record.get("embedding_model"),
            list(record.get("fingerprint") or []),
            list(record.get("queries") or []),
            int(record.get("depth_score") or 0),
            int(record.get("reuse_count") or 0),
            record.get("status") or "active",
            record.get("version") or 1,
            _timestamp(record.get("last_reused_at")),
            _timestamp(record.get("last_reused_at")),
            record.get("created_at") or time.time(),
            record.get("updated_at") or time.time(),
            org_id or record.get("org_id"),
            user_id or record.get("created_by"),
        ),
    )


def import_jsonl(path: str | Path) -> int:
    source = Path(path)
    if not source.is_file():
        return 0
    imported = 0
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            legacy = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(legacy, dict) or not legacy.get("goal"):
            continue
        record = {
            **legacy,
            "id": str(legacy.get("id") or uuid.uuid4()),
            "embedding_model": legacy.get("embedding_model") or _embedding_model(),
            "status": "active",
            "created_at": _timestamp(legacy.get("created_at")) or time.time(),
            "updated_at": _timestamp(legacy.get("updated_at")) or time.time(),
        }
        if _backend() == "memory":
            with _STORE_LOCK:
                if not any(row.get("id") == record["id"] for row in _MEMORY):
                    _MEMORY.append(record)
                    imported += 1
            continue
        with transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM knowledge_records WHERE id = %s", (record["id"],)
            ).fetchone()
            if not exists:
                _insert(conn, record)
                imported += 1
    return imported


def mark_reused(record_id: str) -> None:
    if not record_id:
        return
    if _backend() == "memory":
        with _STORE_LOCK:
            for row in _MEMORY:
                if row.get("id") == record_id and row.get("status") == "active":
                    row["reuse_count"] = int(row.get("reuse_count") or 0) + 1
                    row["last_reused_at"] = time.time()
                    return
        return
    try:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE knowledge_records
                SET reuse_count = reuse_count + 1,
                    last_reused_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND active = TRUE
                """,
                (record_id,),
            )
    except Exception:
        if settings.persistence_required:
            raise
        logger.exception("knowledge_mark_reused_failed")


def stats(org_id: str | None = None) -> dict[str, Any]:
    records = load_records(org_id)
    return {
        "records": len(records),
        "org_id": org_id,
        "backend": _backend(),
        "total_reuse": sum(int(r.get("reuse_count") or 0) for r in records),
        "avg_depth": round(
            sum(int(r.get("depth_score") or 0) for r in records) / max(1, len(records)),
            1,
        ),
        "items": [
            {
                "id": row.get("id"),
                "goal": row.get("goal"),
                "version": row.get("version"),
                "reuse_count": row.get("reuse_count"),
                "depth_score": row.get("depth_score"),
                "sources": len(row.get("citations") or []),
                "updated_at": row.get("updated_at"),
            }
            for row in sorted(
                records,
                key=lambda item: _timestamp(item.get("updated_at")),
                reverse=True,
            )[:50]
        ],
    }


def _record_from_report(
    goal: str, report: dict[str, Any], coverage: dict[str, Any] | None
) -> dict[str, Any]:
    metrics = report.get("metrics") or {}
    depth = _depth_of(metrics, coverage)
    now = time.time()
    return {
        "id": str(uuid.uuid4()),
        "goal": goal[:600],
        "queries": [goal[:600]],
        "fingerprint": sorted(query_fingerprint(goal)),
        "embedding": _embed(goal),
        "embedding_model": _embedding_model(),
        "title": (report.get("title") or "")[:300],
        "executive_summary": (report.get("executive_summary") or "")[:8000],
        "body_markdown": (report.get("body_markdown") or "")[:MAX_BODY_CHARS],
        "decision_rule": (report.get("decision_rule") or "")[:4000],
        "open_questions": list(report.get("open_questions") or [])[:12],
        "limitations": list(report.get("limitations") or [])[:12],
        "claims": _slim_claims(report.get("claims") or []),
        "citations": _slim_citations(report.get("citations") or []),
        "slots": _slim_slots((coverage or {}).get("slots") or []),
        "depth_score": depth,
        "query_type": metrics.get("query_type") or report.get("query_type") or "factual",
        "version": 1,
        "reuse_count": 0,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "history": [
            {"at": now, "depth": depth, "sources": len(report.get("citations") or [])}
        ],
    }


def _depth_of(metrics: dict[str, Any], coverage: dict[str, Any] | None) -> int:
    for candidate in (
        metrics.get("depth_score"),
        ((coverage or {}).get("depth_score") or {}).get("score"),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            pass
    return 0


def _slim_claims(claims: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "slot_id": claim.get("slot_id") or "",
            "text": (claim.get("text") or "")[:400],
            "quote": (claim.get("quote") or "")[:400],
            "url": claim.get("url") or "",
            "tier": claim.get("tier") or "",
            "confidence": float(claim.get("confidence") or 0.5),
            "status": claim.get("status") or "",
        }
        for claim in claims[:16]
        if isinstance(claim, dict)
    ]


def _citation_key(citation: dict[str, Any]) -> str:
    from app.domain.coverage import canonical_source_key

    return canonical_source_key(citation.get("url") or "", citation.get("title") or "")


def _slim_citations(citations: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        key = _citation_key(citation)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "evidence_id": citation.get("evidence_id") or "",
                "title": (citation.get("title") or "")[:300],
                "url": citation.get("url") or "",
                "quote": (citation.get("quote") or "")[:500],
                "tier": citation.get("tier") or "",
                "host": citation.get("host") or "",
            }
        )
        if len(out) >= MAX_CITATIONS:
            break
    return out


def _slim_slots(slots: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": slot.get("id") or "",
            "label": (slot.get("label") or "")[:160],
            "status": slot.get("status") or "open",
            "critical": bool(slot.get("critical")),
        }
        for slot in slots
        if isinstance(slot, dict)
    ]


_STATUS_RANK = {"covered": 3, "weak": 2, "open": 1, "": 0}


def _merge(prior: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    better = (
        fresh
        if int(fresh.get("depth_score") or 0) >= int(prior.get("depth_score") or 0)
        else prior
    )
    citations = list(prior.get("citations") or [])
    seen = {_citation_key(c) for c in citations if isinstance(c, dict)}
    for citation in fresh.get("citations") or []:
        key = _citation_key(citation)
        if key and key not in seen:
            citations.append(citation)
            seen.add(key)
    claims: dict[str, dict[str, Any]] = {}
    for source in (prior.get("claims") or [], fresh.get("claims") or []):
        for claim in source:
            key = claim.get("slot_id") or claim.get("text", "")[:60]
            if key not in claims or float(claim.get("confidence") or 0) > float(
                claims[key].get("confidence") or 0
            ):
                claims[key] = claim
    slots = {
        slot.get("id"): slot
        for slot in prior.get("slots") or []
        if isinstance(slot, dict)
    }
    for slot in fresh.get("slots") or []:
        old = slots.get(slot.get("id"))
        if old is None or _STATUS_RANK.get(
            slot.get("status") or "", 0
        ) >= _STATUS_RANK.get(old.get("status") or "", 0):
            slots[slot.get("id")] = slot
    now = time.time()
    return {
        **prior,
        "id": fresh["id"],
        "goal": better.get("goal") or prior.get("goal"),
        "queries": list(
            dict.fromkeys(
                [*(prior.get("queries") or []), *(fresh.get("queries") or [])]
            )
        )[-12:],
        "fingerprint": sorted(
            set(prior.get("fingerprint") or []) | set(fresh.get("fingerprint") or [])
        ),
        "embedding": better.get("embedding") or prior.get("embedding"),
        "embedding_model": better.get("embedding_model")
        or prior.get("embedding_model"),
        "title": better.get("title") or prior.get("title"),
        "executive_summary": better.get("executive_summary")
        or prior.get("executive_summary"),
        "body_markdown": better.get("body_markdown") or prior.get("body_markdown"),
        "decision_rule": better.get("decision_rule") or prior.get("decision_rule"),
        "open_questions": better.get("open_questions") or prior.get("open_questions"),
        "limitations": better.get("limitations") or prior.get("limitations"),
        "claims": list(claims.values())[:16],
        "citations": citations[:MAX_CITATIONS],
        "slots": list(slots.values()),
        "depth_score": max(
            int(prior.get("depth_score") or 0), int(fresh.get("depth_score") or 0)
        ),
        "version": int(prior.get("version") or 1) + 1,
        "reuse_count": int(prior.get("reuse_count") or 0),
        "status": "active",
        "created_at": prior.get("created_at") or now,
        "updated_at": now,
        "history": [*(prior.get("history") or []), *(fresh.get("history") or [])][-20:],
    }
