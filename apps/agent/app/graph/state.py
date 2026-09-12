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


def overwrite(_left: Any, right: Any) -> Any:
    return right


def merge_unique_evidence(left: list[dict], right: list[dict]) -> list[dict]:
    """Merge evidence lists, deduping by id/url — field-by-field, not row-wins.

    enrich only ever re-fetches URLs already present in `evidence`, so its
    fetched row for a given url/id always arrives *after* collector's
    original snippet-only row for that same identity. A plain "first seen
    wins" set here would keep the original and silently drop every
    successful full-text fetch on every run. Instead, later duplicates are
    merged into the kept row (any truthy field overlays the existing one),
    so e.g. full_text from enrich lands on top of the original search
    snippet without losing fields (like search_snippet) the new row doesn't
    carry.
    """
    from app.graph.serde import pythonize

    seen: dict[str, int] = {}
    out: list[dict] = []
    for item in (left or []) + (right or []):
        item = pythonize(item)
        key = item.get("id") or ""
        url_key = (item.get("url") or "").rstrip("/").lower()
        idx = seen.get(key) if key else None
        if idx is None and url_key:
            idx = seen.get(url_key)
        if idx is not None:
            merged = dict(out[idx])
            for field, value in item.items():
                if value:
                    merged[field] = value
            out[idx] = merged
            if key:
                seen[key] = idx
            if url_key:
                seen[url_key] = idx
            continue
        if key:
            seen[key] = len(out)
        if url_key:
            seen[url_key] = len(out)
        out.append(item)
    return out


class ResearchState(TypedDict, total=False):
    query: str
    org_id: str
    user_id: str
    thread_id: str
    last_execution_id: Annotated[str, overwrite]
    started_at: float
    query_type: QueryType | str
    brief: dict[str, Any]
    brief_confirmed: bool
    plan_confirmed: bool
    memo_confirmed: bool
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
    status: Annotated[str, overwrite]
    out_of_scope: bool
    llm_mode: str
    # Answer reuse from the knowledge store
    reuse_mode: str
    reuse_similarity: float
    reuse_age_days: float
    prior_knowledge: dict[str, Any]
    prior_knowledge_id: str
    integrity_retries: int
    depth_retries: int
    gap_micro_retries: int
    quality_regeneration_count: int
    quality_gate_issues: list[str]
    _quality_history: list[dict]
    _unproductive_gap_ids: list[str]
    _last_followup_gap_ids: list[str]
    mandatory_retrieval_retries: int
    research_contract: dict[str, Any]


def budget_from(state: ResearchState) -> Budget:
    raw = state.get("budget") or {}
    return Budget.model_validate(raw) if raw else Budget()


def plan_from(state: ResearchState) -> Plan | None:
    raw = state.get("plan")
    return Plan.model_validate(raw) if raw else None
