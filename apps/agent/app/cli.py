"""Run a research question without the HTTP stack (auto-approves HITL)."""

from __future__ import annotations

import argparse
import json

from app.domain.schema import Budget
from app.graph.builder import build_test_graph
from app.retrieval.store import ingest_corpus


def main() -> None:
    parser = argparse.ArgumentParser(prog="kiln")
    parser.add_argument("query")
    parser.add_argument(
        "--hitl",
        action="store_true",
        help="Pause for human approval (needs a checkpointer).",
    )
    args = parser.parse_args()
    ingest_corpus()
    graph = build_test_graph(enable_hitl=args.hitl)
    result = graph.invoke(
        {
            "query": args.query,
            "evidence": [],
            "traces": [],
            "budget": Budget().model_dump(),
        },
        config={"configurable": {"thread_id": "cli"}},
    )
    report = result.get("report") or result
    print(json.dumps(report, indent=2, default=str)[:12000])


if __name__ == "__main__":
    main()
