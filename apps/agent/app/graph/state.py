from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from app.domain.schema import (
    Budget,
    Claim,
    CriticVerdict,
    Evidence,
    HumanDecision,
    Plan,
    QueryType,
    Report,
)


def merge_unique_evidence(left: list[dict], right: list[dict]) -> list[dict]:
    from app.graph.serde import pythonize

    seen: set[str] = set()
    out: list[dict] = []
    for item in (left or []) + (right or []):
        item = pythonize(item)
        key = item.get("id") or ""
        url_key = (item.get("url") or "").rstrip("/").lower()
        if (key and key in seen) or (url_key and url_key in seen):
            continue
        if key:
            seen.add(key)
        if url_key:
            seen.add(url_key)
        out.append(item)
    return out


class ResearchState(TypedDict, total=False):
    query: str
    thread_id: str
    last_execution_id: str
    started_at: float
    query_type: QueryType | str
    brief: dict[str, Any]
    brief_confirmed: bool
    plan: dict[str, Any]
    agents_to_run: list[str]
    evidence: Annotated[list[dict], merge_unique_evidence]
    retrieved: list[dict]
    citations: list[dict]
    claims: list[dict]
    critic: dict[str, Any]
    followups: list[dict]
    terminal_followups: list[dict]
    budget: dict[str, Any]
    human_decision: dict[str, Any]
    report: dict[str, Any]
    traces: Annotated[list[dict], operator.add]
    status: str
    out_of_scope: bool
    llm_mode: str
    # Answer reuse from the knowledge store
    reuse_mode: str
    reuse_similarity: float
    reuse_age_days: float
    prior_knowledge: dict[str, Any]
    prior_knowledge_id: str


def budget_from(state: ResearchState) -> Budget:
    raw = state.get("budget") or {}
    return Budget.model_validate(raw) if raw else Budget()


def plan_from(state: ResearchState) -> Plan | None:
    raw = state.get("plan")
    return Plan.model_validate(raw) if raw else None
