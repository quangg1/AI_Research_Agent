from __future__ import annotations

import json
from pathlib import Path


def _prices_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "scenarios" / "prices.json"
        if candidate.exists():
            return candidate
    return Path("../../data/scenarios/prices.json")


def load_prices() -> dict:
    return json.loads(_prices_path().read_text(encoding="utf-8"))


def llm_cost(
    *,
    input_tokens_per_day: float,
    output_tokens_per_day: float,
    model: str = "api_70b",
    days: int = 30,
) -> dict:
    prices = load_prices()
    spec = prices["models"].get(model) or prices["models"]["api_70b"]
    daily_api = 0.0
    monthly_gpu = 0.0
    notes: list[str] = []
    if "input_per_m" in spec:
        daily_api = (input_tokens_per_day / 1_000_000) * spec["input_per_m"] + (
            output_tokens_per_day / 1_000_000
        ) * spec["output_per_m"]
        monthly = round(daily_api * days, 2)
        notes.append("API billed on tokens. Prefill-heavy RAG inflates input tokens.")
        return {
            "model": model,
            "label": spec["label"],
            "daily_usd": round(daily_api, 2),
            "monthly_usd": monthly,
            "kind": "api",
            "notes": notes,
            "inputs": {
                "input_tokens_per_day": input_tokens_per_day,
                "output_tokens_per_day": output_tokens_per_day,
                "days": days,
            },
        }
    tokens_day = input_tokens_per_day + output_tokens_per_day
    hours = tokens_day / max(spec.get("tokens_per_sec") or 1, 1) / 3600
    hours = max(hours, 1.0)
    daily = hours * spec["gpu_hourly"]
    monthly = round(daily * days, 2)
    notes.append("Self-host cost is GPU time, not tokens. Idle capacity still bills.")
    return {
        "model": model,
        "label": spec["label"],
        "daily_usd": round(daily, 2),
        "monthly_usd": monthly,
        "kind": "selfhost",
        "gpu_hours_per_day": round(hours, 2),
        "notes": notes,
        "inputs": {
            "input_tokens_per_day": input_tokens_per_day,
            "output_tokens_per_day": output_tokens_per_day,
            "days": days,
        },
    }


def compare_serving(
    input_tokens_per_day: float,
    output_tokens_per_day: float,
    days: int = 30,
) -> dict:
    models = list(load_prices()["models"].keys())
    rows = [
        llm_cost(
            input_tokens_per_day=input_tokens_per_day,
            output_tokens_per_day=output_tokens_per_day,
            model=m,
            days=days,
        )
        for m in models
    ]
    cheapest = min(rows, key=lambda r: r["monthly_usd"])
    return {
        "rows": rows,
        "cheapest": cheapest["model"],
        "research_query": (
            f"Compare self-hosting vs API for {input_tokens_per_day:.0f} input and "
            f"{output_tokens_per_day:.0f} output tokens/day over {days} days."
        ),
    }


def rag_tradeoff(
    *,
    queries_per_month: float,
    corpus_tokens: float,
    refresh_jobs_per_month: float = 4,
    long_context_tokens: float = 32_000,
) -> dict:
    rag = load_prices()["rag"]
    vector = rag["vector_db_monthly"] + queries_per_month / 1000 * rag["rerank_per_1k_queries"]
    bm25 = rag["bm25_monthly"] + queries_per_month / 1000 * rag["rerank_per_1k_queries"]
    finetune = rag["finetune_job"] * refresh_jobs_per_month
    long_ctx = (queries_per_month * long_context_tokens / 1_000_000) * rag["long_context_input_per_m"]
    rows = [
        {
            "id": "hybrid_rag",
            "label": "Hybrid RAG (BM25 + dense + rerank)",
            "monthly_usd": round(vector, 2),
            "notes": "Best default when facts change. Re-embed only the delta.",
        },
        {
            "id": "bm25",
            "label": "BM25 + rerank (no vector DB)",
            "monthly_usd": round(bm25, 2),
            "notes": "Often enough under ~1M chunks and identifier-heavy corpora.",
        },
        {
            "id": "finetune",
            "label": "Fine-tune refresh jobs",
            "monthly_usd": round(finetune, 2),
            "notes": "Poor for weekly facts; useful for format/style.",
        },
        {
            "id": "long_context",
            "label": f"Long context ({int(long_context_tokens/1000)}k tokens / query)",
            "monthly_usd": round(long_ctx, 2),
            "notes": f"Prefill cost on ~{int(corpus_tokens):,} corpus tokens still applies if you stuff the window.",
        },
    ]
    cheapest = min(rows, key=lambda r: r["monthly_usd"])
    return {
        "rows": rows,
        "cheapest": cheapest["id"],
        "research_query": (
            f"Compare RAG vs fine-tune vs long-context for {int(queries_per_month)} queries/month "
            f"on a {int(corpus_tokens)}-token corpus."
        ),
    }
