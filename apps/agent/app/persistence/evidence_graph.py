"""Persist the claim/source graph. Failures must not break the run."""

from __future__ import annotations

import uuid
from typing import Any

from app.observability.logging import event, logger
from app.persistence.postgres import transaction


def persist_evidence_graph(run_id: str, graph: dict[str, Any]) -> bool:
    if not run_id or not graph:
        return False
    try:
        run_uuid = uuid.UUID(str(run_id))
    except ValueError:
        return False
    sources = graph.get("_sources_full") or []
    claims = graph.get("claims") or []
    edges = graph.get("edges") or []
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM research_claims WHERE run_id = %s", (run_uuid,))
            conn.execute("DELETE FROM research_sources WHERE run_id = %s", (run_uuid,))
            source_ids: dict[str, uuid.UUID] = {}
            for src in sources:
                sid = uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{(src.get('url') or '').lower()}")
                source_ids[str(src.get("id") or src.get("url"))] = sid
                conn.execute(
                    """
                    INSERT INTO research_sources
                      (id, run_id, url, title, host, tier, quality_band, published, content_hash, content_text, retrieved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (run_id, url) DO UPDATE SET
                      title = EXCLUDED.title,
                      content_text = EXCLUDED.content_text,
                      retrieved_at = NOW()
                    """,
                    (
                        sid,
                        run_uuid,
                        src.get("url") or "",
                        (src.get("title") or "")[:300],
                        (src.get("host") or "")[:120],
                        src.get("tier") or "",
                        src.get("quality_band") or "",
                        src.get("published") or "",
                        "",
                        (src.get("content") or "")[:40_000],
                    ),
                )
            claim_ids: dict[str, uuid.UUID] = {}
            for claim in claims:
                key = str(claim.get("id") or claim.get("text") or "")[:40]
                cid = uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:claim:{key}")
                claim_ids[str(claim.get("id") or key)] = cid
                conn.execute(
                    """
                    INSERT INTO research_claims
                      (id, run_id, claim_key, text, kind, confidence, published, quality_band,
                       verification_status, verification_note, locator_label, quote, url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, claim_key) DO UPDATE SET
                      text = EXCLUDED.text,
                      verification_status = EXCLUDED.verification_status,
                      verification_note = EXCLUDED.verification_note,
                      locator_label = EXCLUDED.locator_label
                    """,
                    (
                        cid,
                        run_uuid,
                        key,
                        claim.get("text") or "",
                        claim.get("kind") or "",
                        claim.get("confidence"),
                        claim.get("published") or "",
                        claim.get("quality_band") or "",
                        claim.get("verification_status") or "pending",
                        (claim.get("verification_note") or "")[:500],
                        claim.get("locator") or "",
                        (claim.get("quote") or "")[:800],
                        claim.get("url") or "",
                    ),
                )
            for edge in edges:
                claim_id = claim_ids.get(str(edge.get("claim_id") or ""))
                source_id = source_ids.get(str(edge.get("source_id") or ""))
                if not claim_id:
                    continue
                loc = edge.get("locator") or {}
                span_id = None
                if source_id and (edge.get("quote") or loc.get("found")):
                    span_id = uuid.uuid4()
                    conn.execute(
                        """
                        INSERT INTO research_spans
                          (id, source_id, quote, locator_kind, locator_label, page, char_start, char_end)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            span_id,
                            source_id,
                            (edge.get("quote") or "")[:800],
                            loc.get("kind") or "paragraph",
                            loc.get("label") or "",
                            loc.get("page"),
                            loc.get("char_start"),
                            loc.get("char_end"),
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO research_claim_edges (id, claim_id, source_id, span_id, relation)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (uuid.uuid4(), claim_id, source_id, span_id, edge.get("relation") or "supports"),
                )
        event("evidence_graph_persisted", run_id=run_id, claims=len(claims), sources=len(sources))
        return True
    except Exception as exc:
        logger.warning("evidence_graph_persist_failed %s", exc)
        return False


def compact_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "claims": graph.get("claims") or [],
        "sources": graph.get("sources") or [],
        "edges": [
            {k: v for k, v in edge.items() if k != "quote" or (v or "")[:180]}
            for edge in (graph.get("edges") or [])
        ],
    }
