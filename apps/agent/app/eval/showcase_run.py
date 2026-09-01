"""Full deep research run with showcase budget caps (auto-approves all gates).

Usage:
  SHOWCASE_MODE=true python -m app.eval.showcase_run "Your question?"
  python -m app.eval.showcase_run "..." --output data/showcase/run.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Must set before settings import side-effects in graph nodes.
os.environ.setdefault("SHOWCASE_MODE", "true")
os.environ.setdefault("KNOWLEDGE_BACKEND", "memory")

from app.domain.coverage_gate import gate_from_critic
from app.graph.builder import build_test_graph
from app.observability.logging import logger
from app.retrieval.store import ingest_corpus
from app.runtime import new_budget


def _progress(state: dict) -> None:
    budget = state.get("budget") or {}
    critic = state.get("critic") or {}
    gate = gate_from_critic(critic)
    node = (state.get("traces") or [{}])[-1].get("node") if state.get("traces") else "?"
    print(
        f"[{time.strftime('%H:%M:%S')}] iter={budget.get('iterations', '?')} "
        f"retrieval={budget.get('used_retrieval_calls', 0)}/{budget.get('max_retrieval_calls', '?')} "
        f"enrich={budget.get('used_enrich_calls', 0)}/{budget.get('max_enrich_calls', '?')} "
        f"critic={critic.get('status', '-')} gate={gate.get('gate_reason', '-')} "
        f"last={node}",
        flush=True,
    )


async def _run_graph(graph, query: str, thread_id: str, budget) -> dict:
    last: dict = {}
    async for event in graph.astream(
        {
            "query": query,
            "thread_id": thread_id,
            "evidence": [],
            "traces": [],
            "budget": budget.model_dump(mode="json"),
            "status": "running",
            "reuse_mode": "off",
        },
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="values",
    ):
        if isinstance(event, dict):
            last = event
            _progress(event)
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description="Showcase deep research run (no HITL pauses)")
    parser.add_argument("query", help="Research question")
    parser.add_argument(
        "-o",
        "--output",
        default="data/showcase/last_run.json",
        help="JSON output path (report + metrics)",
    )
    parser.add_argument("--no-ingest", action="store_true", help="Skip corpus ingest")
    args = parser.parse_args()

    if len(args.query.strip()) < 8:
        print("Query too short", file=sys.stderr)
        return 1

    from app.config import settings

    if not settings.showcase_mode:
        os.environ["SHOWCASE_MODE"] = "true"
        # Re-load would need new Settings(); showcase_run always forces via env default above.

    print(
        f"Showcase run | retrieval={settings.showcase_retrieval_pool} "
        f"enrich={settings.showcase_enrich_pool} iterations={settings.showcase_max_iterations}",
        flush=True,
    )

    if not args.no_ingest:
        try:
            n = ingest_corpus()
            print(f"Corpus ingested: {n} chunks", flush=True)
        except Exception as exc:
            logger.warning("corpus_ingest_skipped %s", exc)
            print(f"Corpus ingest skipped: {exc}", flush=True)

    graph = build_test_graph(enable_hitl=False)
    thread_id = str(uuid.uuid4())
    budget = new_budget()
    print(
        f"Budget: retrieval={budget.max_retrieval_calls} enrich={budget.max_enrich_calls} "
        f"max_iterations={budget.max_iterations}",
        flush=True,
    )

    started = time.time()
    last = asyncio.run(_run_graph(graph, args.query.strip(), thread_id, budget))
    elapsed = round(time.time() - started, 1)
    report = last.get("report") or {}
    critic = last.get("critic") or {}
    gate = gate_from_critic(critic)
    budget_out = last.get("budget") or {}

    payload = {
        "query": args.query.strip(),
        "elapsed_s": elapsed,
        "status": last.get("status"),
        "gate_reason": gate.get("gate_reason") or critic.get("gate_reason"),
        "coverage_gate": gate,
        "coverage": critic.get("coverage"),
        "budget": budget_out,
        "report": report,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nDone in {elapsed}s → {out_path}", flush=True)
    print(f"Gate: {payload['gate_reason']}", flush=True)

    body = (report.get("body_markdown") or report.get("executive_summary") or "")[:2000]
    if body:
        print("\n--- Memo preview ---\n", flush=True)
        print(body, flush=True)

    md_path = out_path.with_suffix(".md")
    if report.get("body_markdown"):
        md_path.write_text(str(report["body_markdown"]), encoding="utf-8")
        print(f"\nFull memo: {md_path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
