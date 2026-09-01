"""P0 eval: routing, graph gates, RACE proxy, and compose grounding metrics."""

from __future__ import annotations

import json

from app.domain.research_depth import effective_depth
from app.domain.retrieval_limits import RETRIEVAL_POOL
from app.eval.coverage_metrics import score_compose_pipeline
from app.eval.graph_routing import score_graph_routing
from app.eval.race_proxy import score_race_proxy
from app.eval.runner import load_cases, score_case


def sample_memo_for_query(query: str) -> str:
    goal = query.strip()
    return f"""# Research memo

## At a glance
Structured baseline for eval — not a live synthesis.

## Executive summary
This baseline addresses: {goal[:400]}

## Key findings
1. Placeholder finding pending live run [1]

## Detailed analysis
### Core mechanism
Analysis placeholder for {goal[:120]}.

## Decision rule
Re-run with sufficient model quota for a live memo.

## References
[1] Internal eval placeholder
"""


def run_race_proxy_bench() -> dict:
    rows = []
    for case in load_cases():
        routing = score_case(case)
        query = case["query"]
        memo = sample_memo_for_query(query)
        race = score_race_proxy(
            query,
            memo,
            critic={"depth_score": {"score": 55}, "gate_reason": "sufficient"},
        )
        rows.append(
            {
                "id": case["id"],
                "routing_pass": routing["pass"],
                "depth": effective_depth(),
                "race_proxy": race["overall_proxy"],
                "comprehensiveness": race["comprehensiveness"],
                "instruction_following": race["instruction_following"],
                "readability": race["readability"],
            }
        )
    avg = round(sum(r["race_proxy"] for r in rows) / max(len(rows), 1), 1)
    return {"cases": len(rows), "avg_race_proxy": avg, "depth": effective_depth(), "rows": rows}


def run_compose_metrics_bench() -> dict:
    rows = []
    for case in load_cases()[:5]:
        query = case["query"]
        evidence = [
            {
                "id": "ev1",
                "title": "Source",
                "url": "https://arxiv.org/abs/2301.00001",
                "snippet": f"Evidence about {query[:80]}",
                "quote": f"Evidence about {query[:80]}",
                "tier": "peer_reviewed",
                "credibility": 0.8,
            }
        ]
        metrics = score_compose_pipeline(
            query,
            evidence,
            critic={"status": "insufficient", "coverage": {"slots": []}},
        )
        rows.append({"id": case["id"], **metrics})
    return {"cases": len(rows), "rows": rows}


def run_full_eval() -> dict:
    routing_cases = [score_case(c) for c in load_cases()]
    routing_passed = sum(1 for r in routing_cases if r["pass"])
    graph = score_graph_routing()
    race = run_race_proxy_bench()
    compose = run_compose_metrics_bench()
    return {
        "depth": effective_depth(),
        "retrieval_pool": RETRIEVAL_POOL["deep"],
        "routing": {"passed": routing_passed, "total": len(routing_cases)},
        "graph_routing": graph,
        "race_proxy": {"avg": race["avg_race_proxy"], "cases": race["cases"]},
        "compose_metrics": compose,
    }


def main() -> None:
    out = run_full_eval()
    print(json.dumps(out, indent=2))
    if out["routing"]["passed"] < out["routing"]["total"]:
        raise SystemExit(1)
    if out["graph_routing"]["passed"] < out["graph_routing"]["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
