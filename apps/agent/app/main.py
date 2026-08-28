from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import runtime
from app.config import settings
from app.contracts import ExecutionRequest
from app.domain import knowledge
from app.llm.client import llm
from app.llm.providers import PROVIDERS
from app.retrieval.store import corpus_stats, ingest_corpus
from app.scenarios.llm_cost import compare_serving, rag_tradeoff


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.app_env.lower() in {"production", "prod"} and not settings.agent_key():
        raise RuntimeError("AGENT_SHARED_KEY is required in production")
    await runtime.init_runtime()
    try:
        yield
    finally:
        await runtime.close_runtime()


app = FastAPI(title="Kiln Agent", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def require_agent_key(request: Request, call_next):
    if request.url.path != "/health":
        expected = settings.agent_key()
        if expected and not hmac.compare_digest(request.headers.get("X-Agent-Key", "").strip(), expected):
            return JSONResponse({"detail": "invalid agent credentials"}, status_code=401)
        if settings.app_env.lower() in {"production", "prod"} and not expected:
            return JSONResponse({"detail": "agent authentication is not configured"}, status_code=503)
    return await call_next(request)


@app.get("/health")
async def health():
    # Liveness for orchestrators (Render). Deep deps live on /ready.
    return JSONResponse(
        {
            "ok": True,
            "service": "kiln-agent",
            "llm_mode": "platform" if not settings.require_byok() else "byok",
            "byok_required": settings.require_byok(),
            "providers": list(PROVIDERS),
            "platform_providers": settings.platform_configured(),
        },
        status_code=200,
    )


@app.get("/ready")
async def ready():
    state = await runtime.readiness()
    body = {
        **state,
        "service": "kiln-agent",
        "llm_mode": "platform" if not settings.require_byok() else "byok",
        "byok_required": settings.require_byok(),
        "providers": list(PROVIDERS),
        "platform_providers": settings.platform_configured(),
        "tracing": runtime.tracing_on(),
    }
    return JSONResponse(body, status_code=200 if state["ok"] else 503)


@app.post("/internal/v1/executions/stream")
async def execute(req: ExecutionRequest):
    async def ndjson():
        async for frame in runtime.stream_execution(req):
            yield json.dumps(frame, ensure_ascii=False, default=str) + "\n"

    return StreamingResponse(
        ndjson(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/runs")
async def legacy_start():
    raise HTTPException(410, "Execution moved to the NestJS API")


@app.post("/v1/runs/{thread_id}/resume")
async def legacy_resume(thread_id: str):
    del thread_id
    raise HTTPException(410, "Execution moved to the NestJS API")


@app.get("/v1/runs/{thread_id}")
async def get_run(thread_id: str):
    run = await runtime.load_run(thread_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.get("/v1/runs/{thread_id}/events")
async def events(thread_id: str):
    del thread_id
    raise HTTPException(410, "Run events are owned by the NestJS API")


@app.get("/v1/runs/{thread_id}/checkpoints")
async def checkpoints(thread_id: str):
    if not await runtime.load_run(thread_id):
        raise HTTPException(404, "run not found")
    return {"thread_id": thread_id, "checkpoints": await runtime.list_checkpoints(thread_id)}


@app.get("/v1/corpus")
async def corpus():
    return corpus_stats()


@app.post("/v1/corpus/refresh")
async def corpus_refresh():
    count = await asyncio.to_thread(ingest_corpus)
    return {"ok": True, "documents": count, **{k: v for k, v in corpus_stats().items() if k != "items"}}


@app.get("/v1/knowledge")
async def knowledge_stats():
    return await asyncio.to_thread(knowledge.stats)


@app.get("/v1/knowledge/match")
async def knowledge_match(query: str):
    hit = await asyncio.to_thread(knowledge.lookup, query)
    if not hit:
        return {"match": False}
    return {
        "match": True,
        "mode": hit.mode,
        "similarity": hit.similarity,
        "age_days": hit.age_days,
        "id": hit.record.get("id"),
        "goal": hit.record.get("goal"),
        "version": hit.record.get("version"),
        "sources": len(hit.record.get("citations") or []),
    }


class ServingRequest(BaseModel):
    input_tokens_per_day: float = 8_000_000
    output_tokens_per_day: float = 2_000_000
    days: int = 30


class RagRequest(BaseModel):
    queries_per_month: float = 50_000
    corpus_tokens: float = 2_000_000
    refresh_jobs_per_month: float = 4
    long_context_tokens: float = 32_000


@app.post("/v1/scenarios/serving")
async def scenario_serving(req: ServingRequest):
    return compare_serving(req.input_tokens_per_day, req.output_tokens_per_day, req.days)


@app.post("/v1/scenarios/rag")
async def scenario_rag(req: RagRequest):
    return rag_tradeoff(
        queries_per_month=req.queries_per_month,
        corpus_tokens=req.corpus_tokens,
        refresh_jobs_per_month=req.refresh_jobs_per_month,
        long_context_tokens=req.long_context_tokens,
    )
