from __future__ import annotations

import json
from pathlib import Path

from app.domain.grounding import FORBIDDEN, verify_claim
from app.domain.routing_policy import classify_query, heuristic_plan, out_of_scope


def _eval_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "eval" / "golden_set.json"
        if candidate.exists():
            return candidate
    return Path("../../data/eval/golden_set.json")


def load_cases() -> list[dict]:
    return json.loads(_eval_path().read_text(encoding="utf-8"))["cases"]


def score_case(case: dict) -> dict:
    query = case["query"]
    expected = case["expected_type"]
    if expected == "out_of_scope":
        ok = out_of_scope(query)
        return {"id": case["id"], "pass": ok, "got": "out_of_scope" if ok else classify_query(query).value}

    qtype = classify_query(query)
    plan = heuristic_plan(query, remaining_calls=12)
    type_ok = qtype.value == expected
    agents = [a.value for a in plan.agents_to_run]
    expected_agents = case.get("expected_agents") or []
    agent_ok = all(a in agents for a in expected_agents) if expected_agents else True
    if case.get("must_not_always_fanout") and expected == "factual":
        agent_ok = agent_ok and set(agents) != {"search", "scholar", "docs"}
    tier_agents = {
        "peer_reviewed": {"scholar"},
        "official_regulation": {"docs", "search"},
        "standard_body": {"docs", "search"},
        "intergovernmental": {"docs", "search"},
    }
    tiers_ok = all(bool(set(agents) & tier_agents.get(tier, set())) for tier in case.get("required_tiers") or [])
    myth_ok = True
    forbidden_claims = case.get("forbidden_claims") or []
    for phrase in forbidden_claims:
        fake = verify_claim(
            {"id": "myth", "text": phrase, "support_ids": ["e1"], "confidence": 0.9},
            [{"id": "e1", "snippet": "unrelated", "quote": "unrelated", "title": "x"}],
        )
        if fake.get("grounded"):
            myth_ok = False
        if not any(p.search(phrase) for p in FORBIDDEN) and "always" in phrase.lower():
            # phrase should still be treated as a shipping risk even if regex is looser
            pass
    contradiction_ok = (
        not case.get("expect_contradiction")
        or qtype.value in {"open_research", "comparison"}
        or bool(forbidden_claims and myth_ok)
    )
    return {
        "id": case["id"],
        "pass": type_ok and agent_ok and tiers_ok and myth_ok and contradiction_ok,
        "got": qtype.value,
        "agents": agents,
        "expected_type": expected,
        "tiers_ok": tiers_ok,
        "myth_ok": myth_ok,
        "contradiction_ok": contradiction_ok,
    }


def score_report_quality(query: str, evidence: list[dict]) -> dict:
    from app.domain.grounding import verify_claims
    from app.report.compose import compose_report

    report = compose_report(query=query, evidence=evidence, critic={"status": "sufficient"}, llm_mode="heuristic")
    verified = verify_claims(report.claims, evidence)
    grounded = sum(1 for c in verified if c.get("grounded"))
    cited = len(report.citations)
    halluc = sum(1 for c in verified if not c.get("grounded"))
    return {
        "citations": cited,
        "claims": len(verified),
        "grounded": grounded,
        "hallucinated": halluc,
        "citation_accuracy": round(grounded / max(len(verified), 1), 3),
        "has_memo": bool(report.body_markdown) and "Decision rule" in report.body_markdown,
    }


def main() -> None:
    rows = [score_case(c) for c in load_cases()]
    passed = sum(1 for r in rows if r["pass"])
    print(json.dumps({"passed": passed, "total": len(rows), "rows": rows}, indent=2))
    if passed < len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
