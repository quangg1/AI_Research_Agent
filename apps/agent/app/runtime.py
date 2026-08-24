from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator

from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter

from app.config import settings
from app.contracts import ExecutionFrame, ExecutionRequest
from app.domain import knowledge
from app.domain.schema import Budget
from app.graph.builder import build_graph, build_test_graph
from app.llm.client import CreditsExhaustedError, LLMClient, bind_llm, enable_langsmith, get_llm, llm, reset_llm
from app.llm.redact import scrub_obj, scrub_text
from app.observability.logging import event, logger
from app.persistence import postgres
from app.retrieval.store import ingest_corpus

_graph = None
_pool: AsyncConnectionPool | None = None
_checkpointer = None
_tracing = False
_request_adapter = TypeAdapter(ExecutionRequest)

PIPELINE = [
    "briefing",
    "planner",
    "docs",
    "scholar",
    "search",
    "collector",
    "enrich",
    "retrieve",
    "extract",
    "critic",
    "hitl",
    "report",
]
NODE_HINTS = {
    "briefing": "Drafting an editable research plan",
    "planner": "Calling your model to decompose the question — this can take a couple of minutes",
    "docs": "Reading primary docs and framework pages",
    "scholar": "Pulling systems papers",
    "search": "Searching current web sources",
    "collector": "Merging evidence into one working set",
    "enrich": "Fetching full documents",
    "retrieve": "Ranking passages by relevance",
    "extract": "Building the quote and citation ledger",
    "critic": "Checking conflicts before the memo",
    "hitl": "Waiting for your review",
    "report": "Writing the memo — your model stays on this step until the draft is done",
}
NODE_ETA_S = {
    "briefing": 8,
    "planner": 90,
    "docs": 8,
    "scholar": 8,
    "search": 12,
    "collector": 3,
    "enrich": 10,
    "retrieve": 5,
    "extract": 4,
    "critic": 45,
    "hitl": 0,
    "report": 120,
}
_TERMINAL = {"completed", "cancelled", "out_of_scope"}


async def init_runtime() -> None:
    global _graph, _pool, _checkpointer, _tracing
    _tracing = enable_langsmith()
    try:
        await asyncio.to_thread(postgres.open_pool)
        await asyncio.to_thread(ingest_corpus)
        if settings.knowledge_import_path:
            imported = await asyncio.to_thread(
                knowledge.import_jsonl, settings.knowledge_import_path
            )
            event("knowledge_import", records=imported)

        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        _pool = AsyncConnectionPool(
            conninfo=postgres.psycopg_url(settings.database_url),
            min_size=1,
            max_size=8,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            open=False,
        )
        await _pool.open()
        _checkpointer = AsyncPostgresSaver(_pool)
        await _checkpointer.setup()
        _graph = build_graph(_checkpointer)
        event("checkpointer", backend="postgres")
    except Exception:
        await _close_pools()
        _checkpointer = None
        _graph = None
        if settings.persistence_required:
            raise
        if (
            os.getenv("KNOWLEDGE_BACKEND", settings.knowledge_backend).lower()
            == "memory"
        ):
            _graph = build_test_graph()
            logger.warning("runtime_using_explicit_test_memory")
        else:
            raise


async def _close_pools() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
    await asyncio.to_thread(postgres.close_pool)


async def close_runtime() -> None:
    global _graph, _checkpointer
    await _close_pools()
    _graph = None
    _checkpointer = None


def tracing_on() -> bool:
    return bool(_tracing)


def graph():
    if _graph is None:
        raise RuntimeError("Agent runtime is not initialized")
    return _graph


def new_budget() -> Budget:
    return Budget(
        max_tool_calls=settings.max_tool_calls,
        max_tokens=settings.max_input_tokens,
        max_iterations=settings.max_iterations,
    )


def _advisory_key(run_id: str) -> int:
    raw = int.from_bytes(hashlib.sha256(run_id.encode("utf-8")).digest()[:8], "big")
    return raw - (1 << 64) if raw >= (1 << 63) else raw


async def stream_execution(
    request: ExecutionRequest | dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    sequence = 0
    async for frame in _stream_execution(request):
        frame["sequence"] = sequence
        sequence += 1
        yield scrub_obj(frame)


async def _stream_execution(
    request: ExecutionRequest | dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    req = _request_adapter.validate_python(request)
    run_id = req.run_id
    execution_id = req.execution_id
    sequence = 0
    client, cred_error = _client_for_request(req)
    if cred_error:
        yield _frame(
            "error",
            run_id,
            execution_id,
            sequence=sequence,
            error=cred_error,
            retryable=False,
        )
        return
    token = bind_llm(client)
    try:
        async for frame in _stream_execution_bound(req):
            yield frame
    finally:
        reset_llm(token)
        client.close()


def _client_for_request(req) -> tuple[LLMClient, str | None]:
    cred = getattr(req, "llm", None)
    if cred is not None:
        client = LLMClient.from_credential(cred)
        if not client.available:
            provider = str(getattr(cred, "provider", "") or "model")
            return client, (
                f"{provider} is not configured on this server. Paste your own API key to continue. "
                "Kiln never stores visitor keys."
            )
        return client, None
    if settings.require_byok():
        return LLMClient(use_env=False), (
            "A model API key is required. Paste your Gemini, OpenAI, or Grok key. "
            "Kiln never stores visitor keys."
        )
    return LLMClient(), None


async def _stream_execution_bound(req) -> AsyncIterator[dict[str, Any]]:
    run_id = req.run_id
    execution_id = req.execution_id
    sequence = 0
    if _pool is None:
        yield _frame(
            "error",
            run_id,
            execution_id,
            sequence=sequence,
            error="runtime is not ready",
            retryable=True,
        )
        return
    lock_key = _advisory_key(run_id)
    try:
        async with _pool.connection() as lock_conn:
            await lock_conn.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
            try:
                async for frame in _stream_locked(req):
                    frame["sequence"] = sequence
                    sequence += 1
                    yield frame
            finally:
                await lock_conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
    except asyncio.CancelledError:
        raise
    except CreditsExhaustedError as exc:
        snapshot = await _safe_snapshot(run_id)
        # Build a lightweight namespace so the helper can read .values / .next
        snap = SimpleNamespace(
            values=(snapshot or {}).get("values") or {},
            next=(snapshot or {}).get("next") or [],
            tasks=[],
            config={"configurable": {"checkpoint_id": (snapshot or {}).get("checkpoint_id")}},
        )
        frame = _credits_interrupt_frame(run_id, execution_id, snap, exc)
        frame["sequence"] = sequence
        if snapshot:
            # Preserve annotated progress fields from the last safe snapshot.
            merged = dict(snapshot)
            merged["interrupt"] = frame.get("interrupt") or frame.get("data")
            merged["status"] = "awaiting_human"
            frame["snapshot"] = _annotate(merged)
        yield frame
    except Exception as exc:
        logger.exception("execution_stream_failed run_id=%s", run_id)
        snapshot = await _safe_snapshot(run_id)
        extra = tuple(
            slot.api_key
            for slot in getattr(get_llm(), "_slots", [])
            if getattr(slot, "api_key", "")
        ) or (getattr(get_llm(), "_api_key", "") or "",)
        # Credits messages that leaked as generic exceptions still park, not fail.
        msg = scrub_text(str(exc), extra)
        if _looks_like_credits_error(msg):
            snap = SimpleNamespace(
                values=(snapshot or {}).get("values") or {},
                next=(snapshot or {}).get("next") or [],
                tasks=[],
                config={"configurable": {}},
            )
            frame = _credits_interrupt_frame_from_message(
                run_id, execution_id, snap, msg, provider="gemini", tried=["gemini"]
            )
            frame["sequence"] = sequence
            if snapshot:
                merged = dict(snapshot)
                merged["interrupt"] = frame.get("data")
                merged["status"] = "awaiting_human"
                frame["snapshot"] = _annotate(merged)
            yield frame
            return
        yield _frame(
            "error",
            run_id,
            execution_id,
            sequence=sequence,
            snapshot=snapshot,
            error=msg,
            retryable=_retryable(exc),
        )


async def _stream_locked(req) -> AsyncIterator[dict[str, Any]]:
    run_id = req.run_id
    execution_id = req.execution_id
    config = {"configurable": {"thread_id": run_id}}
    snapshot = await graph().aget_state(config)
    values = dict(getattr(snapshot, "values", None) or {})
    interrupt = _interrupt_payload(snapshot)
    status = values.get("status")

    if status in _TERMINAL:
        yield _frame(
            "terminal",
            run_id,
            execution_id,
            snapshot=_snapshot_dict(snapshot, run_id),
            data=values.get("report"),
            status=status,
        )
        return
    decision = {}
    if req.kind == "resume":
        decision = req.decision.model_dump(by_alias=True)
    action = str(decision.get("action") or "").strip().lower()
    credits_continue = action in {"continue", "retry_credits"}

    if status == "failed":
        # Credits failures are parked for Continue; legacy runs may still be "failed".
        if not (req.kind == "resume" and credits_continue):
            yield _frame(
                "error",
                run_id,
                execution_id,
                snapshot=_snapshot_dict(snapshot, run_id),
                error=str(values.get("error") or "execution failed"),
                retryable=False,
            )
            return

    if req.kind == "start":
        if values:
            nxt = list(getattr(snapshot, "next", None) or [])
            if not interrupt and any(node in {"hitl", "briefing"} for node in nxt):
                interrupt = _interrupt_from_state(values, nxt)
            if interrupt:
                annotated = _snapshot_dict(snapshot, run_id)
                if not annotated.get("interrupt"):
                    annotated["interrupt"] = _jsonable(interrupt)
                    annotated["status"] = "awaiting_human"
                    annotated = _annotate(annotated)
                yield _frame(
                    "interrupt",
                    run_id,
                    execution_id,
                    snapshot=annotated,
                    data=_jsonable(interrupt),
                )
                return
            graph_input: Any = None
        else:
            graph_input = {
                "query": req.query,
                "thread_id": run_id,
                "last_execution_id": execution_id,
                "evidence": [],
                "traces": [],
                "budget": new_budget().model_dump(mode="json"),
                "status": "running",
                "llm_mode": llm.mode,
                "reuse_mode": "off" if req.fresh else "",
                "started_at": time.time(),
            }
    else:
        if not values:
            yield _frame(
                "error",
                run_id,
                execution_id,
                error="cannot resume a run without a checkpoint",
                retryable=False,
            )
            return
        nxt = list(getattr(snapshot, "next", None) or [])
        if not interrupt and any(node in {"hitl", "briefing"} for node in nxt):
            interrupt = _interrupt_from_state(values, nxt)
        itype = interrupt.get("type") if isinstance(interrupt, dict) else None
        lg_parked = bool(_interrupt_payload(snapshot)) or any(
            node in {"hitl", "briefing"} for node in nxt
        )
        if values.get("last_execution_id") == execution_id:
            # Same execution retried after a worker crash — continue, do not re-resume.
            graph_input = None
        elif lg_parked and itype != "credits_exhausted":
            # HITL / brief: resume value flows into interrupt().
            graph_input = Command(
                resume=decision,
                update={"last_execution_id": execution_id, "status": "running", "error": ""},
            )
        elif lg_parked and itype == "credits_exhausted":
            # Credits pause created via interrupt() inside generate — resume that interrupt.
            graph_input = Command(
                resume=decision or {"action": "continue"},
                update={"last_execution_id": execution_id, "status": "running", "error": ""},
            )
        elif credits_continue or itype == "credits_exhausted":
            # Credits exhausted outside interrupt context (e.g. asyncio.to_thread).
            # Checkpoint still points at the failed node — re-enter it with a fresh LLM bind.
            graph_input = Command(
                update={"last_execution_id": execution_id, "status": "running", "error": ""},
            )
        else:
            graph_input = None

    try:
        async for update in _updates_with_heartbeats(
            graph_input, config, run_id, execution_id
        ):
            yield update
    except GraphInterrupt as exc:
        snapshot = await graph().aget_state(config)
        payload = _interrupt_payload(snapshot) or _interrupt_from_exc(exc)
        yield _credits_or_generic_interrupt(run_id, execution_id, snapshot, payload)
        return
    except CreditsExhaustedError as exc:
        # LLM ran outside a LangGraph runnable context (common with to_thread) —
        # park the run at the current checkpoint instead of failing the job.
        snapshot = await graph().aget_state(config)
        yield _credits_interrupt_frame(run_id, execution_id, snapshot, exc)
        return

    snapshot = await graph().aget_state(config)
    payload = _interrupt_payload(snapshot)
    annotated = _snapshot_dict(snapshot, run_id)
    values = dict(getattr(snapshot, "values", None) or {})
    nxt = list(getattr(snapshot, "next", None) or [])
    if not payload and any(node in {"hitl", "briefing"} for node in nxt):
        payload = _interrupt_from_state(values, nxt)
        if payload:
            annotated = dict(annotated)
            annotated["interrupt"] = _jsonable(payload)
            annotated["status"] = "awaiting_human"
            annotated = _annotate(annotated)
    if payload:
        yield _frame(
            "interrupt",
            run_id,
            execution_id,
            snapshot=annotated,
            data=_jsonable(payload),
        )
        return
    status = values.get("status")
    if status == "failed":
        yield _frame(
            "error",
            run_id,
            execution_id,
            snapshot=annotated,
            error=str(values.get("error") or "execution failed"),
            retryable=False,
        )
        return
    if status not in _TERMINAL and not nxt:
        status = "completed"
    if status in _TERMINAL:
        yield _frame(
            "terminal",
            run_id,
            execution_id,
            snapshot=annotated,
            data=values.get("report"),
            status=status,
        )


async def _updates_with_heartbeats(
    graph_input: Any,
    config: dict[str, Any],
    run_id: str,
    execution_id: str,
) -> AsyncIterator[dict[str, Any]]:
    iterator = (
        graph().astream(graph_input, config=config, stream_mode="updates").__aiter__()
    )
    pending: asyncio.Task | None = asyncio.create_task(iterator.__anext__())
    try:
        while pending is not None:
            done, _ = await asyncio.wait({pending}, timeout=2.0)
            if not done:
                snapshot = await graph().aget_state(config)
                yield _frame(
                    "heartbeat",
                    run_id,
                    execution_id,
                    snapshot=_snapshot_dict(snapshot, run_id),
                )
                continue
            try:
                update = pending.result()
            except StopAsyncIteration:
                pending = None
                break
            snapshot = await graph().aget_state(config)
            yield _frame(
                "update",
                run_id,
                execution_id,
                snapshot=_snapshot_dict(snapshot, run_id),
                data=_jsonable(update),
            )
            pending = asyncio.create_task(iterator.__anext__())
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        aclose = getattr(iterator, "aclose", None)
        if aclose:
            await aclose()


def _looks_like_credits_error(message: str) -> bool:
    blob = (message or "").lower()
    return any(
        token in blob
        for token in (
            "out of credits",
            "credits are exhausted",
            "insufficient_quota",
            "quota exceeded",
            "exceeded your current quota",
            "resource_exhausted",
        )
    )


def _credits_payload_from_exc(exc: CreditsExhaustedError, snapshot) -> dict[str, Any]:
    return _credits_payload(
        message=str(exc),
        provider=getattr(exc, "provider", "") or "model",
        tried=list(getattr(exc, "tried", None) or []),
        source=getattr(exc, "source", "") or "platform",
        snapshot=snapshot,
    )


def _credits_payload(
    *,
    message: str,
    provider: str,
    tried: list[str],
    source: str,
    snapshot,
) -> dict[str, Any]:
    nxt = list(getattr(snapshot, "next", None) or [])
    return {
        "type": "credits_exhausted",
        "title": "Model credits exhausted",
        "message": message,
        "provider": provider,
        "tried": tried,
        "source": source,
        "failed_node": nxt[0] if nxt else None,
        "resume_hint": (
            "Paste a new API key or top up credits, then Continue — "
            "research resumes from this step, not from scratch."
        ),
    }


def _credits_interrupt_frame(
    run_id: str, execution_id: str, snapshot, exc: CreditsExhaustedError
) -> dict[str, Any]:
    return _credits_interrupt_frame_from_message(
        run_id,
        execution_id,
        snapshot,
        str(exc),
        provider=getattr(exc, "provider", "") or "model",
        tried=list(getattr(exc, "tried", None) or []),
        source=getattr(exc, "source", "") or "platform",
    )


def _credits_interrupt_frame_from_message(
    run_id: str,
    execution_id: str,
    snapshot,
    message: str,
    *,
    provider: str = "model",
    tried: list[str] | None = None,
    source: str = "platform",
) -> dict[str, Any]:
    payload = _credits_payload(
        message=message,
        provider=provider,
        tried=list(tried or []),
        source=source,
        snapshot=snapshot,
    )
    annotated = _snapshot_dict(snapshot, run_id)
    annotated = dict(annotated)
    annotated["interrupt"] = payload
    annotated["status"] = "awaiting_human"
    if payload.get("failed_node"):
        annotated["current_node"] = payload["failed_node"]
    annotated = _annotate(annotated)
    annotated["hint"] = "Paused for credits — Continue resumes from this step"
    return _frame(
        "interrupt",
        run_id,
        execution_id,
        snapshot=annotated,
        data=payload,
    )


def _credits_or_generic_interrupt(
    run_id: str, execution_id: str, snapshot, payload: Any
) -> dict[str, Any]:
    data = _unwrap_interrupt(payload) or {}
    if isinstance(data, dict) and data.get("type") == "credits_exhausted":
        annotated = _snapshot_dict(snapshot, run_id)
        annotated = dict(annotated)
        annotated["interrupt"] = data
        annotated["status"] = "awaiting_human"
        annotated = _annotate(annotated)
        annotated["hint"] = "Paused for credits — Continue resumes from this step"
        return _frame(
            "interrupt",
            run_id,
            execution_id,
            snapshot=annotated,
            data=_jsonable(data),
        )
    return _frame(
        "interrupt",
        run_id,
        execution_id,
        snapshot=_snapshot_dict(snapshot, run_id),
        data=_jsonable(payload),
    )


def _frame(
    frame_type: str,
    run_id: str,
    execution_id: str,
    *,
    sequence: int = 0,
    snapshot: dict[str, Any] | None = None,
    data: Any = None,
    status: str | None = None,
    error: str | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    interrupt = data if frame_type == "interrupt" else None
    return ExecutionFrame(
        type=frame_type,
        runId=run_id,
        executionId=execution_id,
        sequence=sequence,
        snapshot=snapshot or {},
        data=data,
        interrupt=interrupt,
        status=status,
        error=error,
        retryable=retryable,
        current_node=(snapshot or {}).get("current_node"),
    ).model_dump(mode="json", by_alias=True, exclude_none=True)


async def _safe_snapshot(run_id: str) -> dict[str, Any]:
    try:
        return _snapshot_dict(
            await graph().aget_state({"configurable": {"thread_id": run_id}}),
            run_id,
        )
    except Exception:
        return {}


def _retryable(exc: Exception) -> bool:
    # Credits now park via interrupt when inside a graph; if they still surface
    # as a stream error, do not auto-retry the whole job from scratch.
    if isinstance(exc, CreditsExhaustedError):
        return False
    return not isinstance(exc, (ValueError, KeyError, TypeError))


async def load_run(thread_id: str) -> dict[str, Any] | None:
    try:
        snapshot = await graph().aget_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        return None
    if not getattr(snapshot, "values", None):
        return None
    return _snapshot_dict(snapshot, thread_id)


def get_run(_thread_id: str) -> None:
    return None


async def list_checkpoints(thread_id: str) -> list[dict[str, Any]]:
    config = {"configurable": {"thread_id": thread_id}}
    out: list[dict[str, Any]] = []
    try:
        async for checkpoint in graph().aget_state_history(config):
            values = checkpoint.values or {}
            out.append(
                {
                    "id": checkpoint.config.get("configurable", {}).get(
                        "checkpoint_id"
                    ),
                    "created_at": str(getattr(checkpoint, "created_at", "")),
                    "next": list(checkpoint.next or []),
                    "status": values.get("status"),
                    "iteration": (values.get("budget") or {}).get("iterations"),
                    "nodes": [
                        trace.get("node") for trace in values.get("traces") or []
                    ][-6:],
                }
            )
            if len(out) >= 30:
                break
    except Exception as exc:
        logger.warning("history_failed %s", exc)
    return out


async def readiness() -> dict[str, Any]:
    checkpointer_ok = _checkpointer is not None and _graph is not None
    sync_db = await asyncio.to_thread(postgres.health)
    async_db = False
    if _pool is not None:
        try:
            async with _pool.connection() as conn:
                async_db = await (await conn.execute("SELECT 1")).fetchone() is not None
        except Exception:
            async_db = False
    db_ok = bool(sync_db and async_db)
    return {
        "ok": bool(checkpointer_ok and db_ok),
        "checkpointer": checkpointer_ok,
        "db": db_ok,
    }


def _snapshot_dict(snapshot, run_id: str) -> dict[str, Any]:
    values = _jsonable(dict(getattr(snapshot, "values", None) or {}))
    interrupt = _unwrap_interrupt(_jsonable(_interrupt_payload(snapshot)))
    raw_status = values.get("status")
    if interrupt and raw_status not in _TERMINAL and raw_status != "failed":
        status = "awaiting_human"
    else:
        status = raw_status or ("awaiting_human" if interrupt else "running")
    run = {
        "thread_id": run_id,
        "runId": run_id,
        "status": status,
        "values": values,
        "interrupt": interrupt,
        "started_at": values.get("started_at"),
    }
    annotated = _annotate(run)
    annotated["next"] = list(getattr(snapshot, "next", None) or [])
    annotated["checkpoint_id"] = (
        (getattr(snapshot, "config", None) or {})
        .get("configurable", {})
        .get("checkpoint_id")
    )
    return _jsonable(annotated)


def _next_node_after_resume(run: dict[str, Any], decision: dict[str, Any]) -> str:
    interrupt = run.get("interrupt") or {}
    action = (decision or {}).get("action") or "start"
    if interrupt.get("type") == "research_brief":
        return "briefing" if action == "cancel" else "planner"
    if action == "revise":
        return "planner"
    return "report"


def _annotate(run: dict[str, Any]) -> dict[str, Any]:
    out = dict(run)
    started = float(
        out["started_at"] if out.get("started_at") is not None else time.time()
    )
    elapsed = max(0, int(time.time() - started))
    values = out.get("values") or {}
    traces = values.get("traces") or []
    done_nodes = {trace.get("node") for trace in traces if isinstance(trace, dict)}
    status = out.get("status") or values.get("status") or "running"
    nxt = [str(n) for n in (out.get("next") or [])]
    value_status = str(values.get("status") or "")
    current = out.get("current_node")
    # After HITL resumes, traces still end on "hitl" while report is writing.
    # Prefer the real next node / approved status so the UI does not say "Waiting for review".
    if "report" in nxt or value_status in {"approved", "completed"} or "report" in done_nodes:
        current = "report"
    elif "hitl" in nxt or (status == "awaiting_human" and (out.get("interrupt") or {}).get("type") == "approve_report"):
        current = "hitl"
    elif "briefing" in nxt or (status == "awaiting_human" and (out.get("interrupt") or {}).get("type") == "research_brief"):
        current = "briefing"
    elif status == "awaiting_human" and (out.get("interrupt") or {}).get("type") == "credits_exhausted":
        # Stay on the node that was running when credits ran out.
        current = nxt[0] if nxt else (traces[-1].get("node") if traces else current) or "planner"
    elif not current:
        if status == "awaiting_human":
            interrupt = out.get("interrupt") or {}
            itype = interrupt.get("type")
            if itype == "research_brief":
                current = "briefing"
            elif itype == "credits_exhausted":
                current = nxt[0] if nxt else "planner"
            else:
                current = "hitl"
        elif traces:
            current = traces[-1].get("node") or "planner"
        else:
            current = "briefing"
    if current not in PIPELINE:
        current = "planner"
    index = PIPELINE.index(current)
    completed = sum(1 for name in PIPELINE if name in done_nodes and name != current)
    if status == "completed" or value_status == "completed":
        fraction = float(len(PIPELINE))
    elif status == "awaiting_human":
        fraction = completed + 0.5
    elif current == "report":
        fraction = max(completed, len(PIPELINE) - 1) + 0.55
    else:
        fraction = completed + 0.4
    remaining = sum(NODE_ETA_S.get(name, 8) for name in PIPELINE[index + 1 :])
    out["started_at"] = started
    out["elapsed_s"] = elapsed
    out["current_node"] = current
    out["progress"] = round(min(1.0, fraction / len(PIPELINE)), 3)
    interrupt = out.get("interrupt") or {}
    if interrupt.get("type") == "credits_exhausted" and status == "awaiting_human":
        out["hint"] = "Paused for credits — Continue resumes from this step"
    elif current == "report" and status != "completed" and value_status != "completed":
        out["hint"] = "Writing the long memo"
    else:
        out["hint"] = NODE_HINTS.get(current, "Working…")
    out["eta_s"] = int(NODE_ETA_S.get(current, 15) + remaining * 0.65)
    return out


def _interrupt_from_exc(exc: GraphInterrupt) -> Any:
    interrupts = getattr(exc, "interrupts", None) or exc.args
    first = interrupts[0] if interrupts else None
    return _unwrap_interrupt(getattr(first, "value", first))


def _interrupt_from_state(values: dict[str, Any], nxt: list[str]) -> dict[str, Any]:
    if "briefing" in nxt and not values.get("brief_confirmed"):
        return {
            "type": "research_brief",
            "title": "Research plan",
            "brief": values.get("brief") or {},
        }
    retrieved = values.get("retrieved") or values.get("evidence") or []
    return {
        "type": "approve_report",
        "query": values.get("query"),
        "query_type": values.get("query_type"),
        "plan": values.get("plan"),
        "critic": values.get("critic"),
        "budget": values.get("budget"),
        "llm_mode": values.get("llm_mode"),
        "claims_preview": (values.get("claims") or [])[:4],
        "evidence_preview": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "tier": item.get("tier"),
                "snippet": (item.get("snippet") or "")[:280],
            }
            for item in retrieved[:8]
            if isinstance(item, dict)
        ],
    }


def _interrupt_payload(snapshot) -> Any:
    for task in getattr(snapshot, "tasks", None) or []:
        interrupts = getattr(task, "interrupts", None) or []
        if interrupts:
            return _unwrap_interrupt(getattr(interrupts[0], "value", interrupts[0]))
    values = getattr(snapshot, "values", {}) or {}
    return _unwrap_interrupt(values.get("__interrupt__"))


def _unwrap_interrupt(raw: Any) -> Any:
    cur = raw
    for _ in range(4):
        if isinstance(cur, list) and cur:
            cur = cur[0]
            continue
        if isinstance(cur, dict):
            if cur.get("type"):
                return cur
            inner = cur.get("value")
            if inner is None:
                inner = cur.get("interrupt")
            if inner is None:
                nested = cur.get("interrupts")
                if isinstance(nested, list) and nested:
                    inner = nested[0]
            if inner is not None:
                cur = inner
                continue
        return cur
    return cur


def _jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return scrub_obj(obj)
    except TypeError:
        if isinstance(obj, dict):
            return scrub_obj({str(key): _jsonable(value) for key, value in obj.items()})
        if isinstance(obj, (list, tuple)):
            return scrub_obj([_jsonable(value) for value in obj])
        return scrub_text(str(obj))
