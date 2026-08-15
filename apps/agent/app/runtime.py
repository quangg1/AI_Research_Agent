from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
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
from app.llm.client import enable_langsmith, llm
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
    "planner": "Calling Gemini to decompose the question — this can take a couple of minutes",
    "docs": "Reading primary docs and framework pages",
    "scholar": "Pulling systems papers",
    "search": "Searching current web sources",
    "collector": "Merging evidence into one working set",
    "enrich": "Fetching full documents",
    "retrieve": "Ranking passages by relevance",
    "extract": "Building the quote and citation ledger",
    "critic": "Checking conflicts before the memo",
    "hitl": "Waiting for your review",
    "report": "Writing the memo — Gemini stays on this step until the draft is done",
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
        yield frame


async def _stream_execution(
    request: ExecutionRequest | dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    req = _request_adapter.validate_python(request)
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
    except Exception as exc:
        logger.exception("execution_stream_failed run_id=%s", run_id)
        snapshot = await _safe_snapshot(run_id)
        yield _frame(
            "error",
            run_id,
            execution_id,
            sequence=sequence,
            snapshot=snapshot,
            error=str(exc),
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
    if status == "failed":
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
            if interrupt:
                yield _frame(
                    "interrupt",
                    run_id,
                    execution_id,
                    snapshot=_snapshot_dict(snapshot, run_id),
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
        if not interrupt and values.get("last_execution_id") != execution_id:
            graph_input = None
        elif values.get("last_execution_id") == execution_id:
            graph_input = None
        else:
            graph_input = Command(
                resume=req.decision.model_dump(by_alias=True),
                update={"last_execution_id": execution_id},
            )

    try:
        async for update in _updates_with_heartbeats(
            graph_input, config, run_id, execution_id
        ):
            yield update
    except GraphInterrupt as exc:
        snapshot = await graph().aget_state(config)
        payload = _interrupt_payload(snapshot) or _interrupt_from_exc(exc)
        yield _frame(
            "interrupt",
            run_id,
            execution_id,
            snapshot=_snapshot_dict(snapshot, run_id),
            data=_jsonable(payload),
        )
        return

    snapshot = await graph().aget_state(config)
    payload = _interrupt_payload(snapshot)
    annotated = _snapshot_dict(snapshot, run_id)
    if payload:
        yield _frame(
            "interrupt",
            run_id,
            execution_id,
            snapshot=annotated,
            data=_jsonable(payload),
        )
        return
    values = dict(getattr(snapshot, "values", None) or {})
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
    if status not in _TERMINAL and not list(getattr(snapshot, "next", None) or []):
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
    interrupt = _jsonable(_interrupt_payload(snapshot))
    status = values.get("status") or ("awaiting_human" if interrupt else "running")
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
    traces = (out.get("values") or {}).get("traces") or []
    done_nodes = {trace.get("node") for trace in traces if isinstance(trace, dict)}
    status = out.get("status") or "running"
    current = out.get("current_node")
    if not current:
        if status == "awaiting_human":
            interrupt = out.get("interrupt") or {}
            current = (
                "briefing" if interrupt.get("type") == "research_brief" else "hitl"
            )
        elif "report" in done_nodes:
            current = "report"
        elif traces:
            current = traces[-1].get("node") or "planner"
        else:
            current = "briefing"
    if current not in PIPELINE:
        current = "planner"
    index = PIPELINE.index(current)
    completed = sum(1 for name in PIPELINE if name in done_nodes and name != current)
    if status == "completed":
        fraction = float(len(PIPELINE))
    elif status == "awaiting_human":
        fraction = completed + 0.5
    else:
        fraction = completed + 0.4
    remaining = sum(NODE_ETA_S.get(name, 8) for name in PIPELINE[index + 1 :])
    out["started_at"] = started
    out["elapsed_s"] = elapsed
    out["current_node"] = current
    out["progress"] = round(min(1.0, fraction / len(PIPELINE)), 3)
    out["hint"] = NODE_HINTS.get(current, "Working…")
    out["eta_s"] = int(NODE_ETA_S.get(current, 15) + remaining * 0.65)
    return out


def _interrupt_from_exc(exc: GraphInterrupt) -> Any:
    interrupts = getattr(exc, "interrupts", None) or exc.args
    first = interrupts[0] if interrupts else None
    return getattr(first, "value", first)


def _interrupt_payload(snapshot) -> Any:
    for task in getattr(snapshot, "tasks", None) or []:
        interrupts = getattr(task, "interrupts", None) or []
        if interrupts:
            return getattr(interrupts[0], "value", interrupts[0])
    values = getattr(snapshot, "values", {}) or {}
    return values.get("__interrupt__")


def _jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        if isinstance(obj, dict):
            return {str(key): _jsonable(value) for key, value in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_jsonable(value) for value in obj]
        return str(obj)
