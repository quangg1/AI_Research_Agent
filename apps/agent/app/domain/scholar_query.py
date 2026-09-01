"""Compact OpenAlex/Semantic Scholar–friendly retrieval queries."""

from __future__ import annotations

import re

from app.domain.schema import AgentName, SubQuery
from app.domain.textutil import distinctive_terms, user_goal

_ORCHESTRATION_RE = re.compile(
    r"\b(fsm|rollback|orchestrat|agent loop|mcts|state machine|tool loop)\b", re.I
)
_SUFFIX_NOISE = re.compile(
    r"\b(contrary findings|counterexample|fails to|benchmark results|success rate comparison|"
    r"failure modes|ablation|when fsm|rollback fails)\b",
    re.I,
)
_STOP = frozenset(
    "a an the and or for to of in on with vs is are be how what when why which that this these those "
    "from into about over under than then also only very".split()
)


def topic_terms_from_goal(goal: str, limit: int = 6) -> list[str]:
    terms = distinctive_terms(goal, limit=limit * 2) or []
    out: list[str] = []
    for t in terms:
        low = t.lower().strip()
        if len(low) < 3 or low in _STOP:
            continue
        if low not in out:
            out.append(low)
        if len(out) >= limit:
            break
    return out


def compact_retrieval_query(
    text: str,
    *,
    goal: str = "",
    agent: str = "search",
    max_words: int = 14,
) -> str:
    """Turn planner sub-queries into short keyword queries APIs can match."""
    raw = (text or "").strip()
    if not raw:
        return (goal or "")[:120]

    cleaned = _SUFFIX_NOISE.sub(" ", raw)
    cleaned = re.sub(r"\bor\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"[\"'`]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    words = [w for w in re.split(r"\W+", cleaned) if w and w.lower() not in _STOP]
    if len(words) > max_words:
        # Prefer distinctive goal terms at the front for scholar.
        goal_terms = topic_terms_from_goal(goal or raw, limit=max_words)
        if goal_terms:
            merged: list[str] = []
            seen: set[str] = set()
            for w in goal_terms + [w.lower() for w in words]:
                if w in seen:
                    continue
                seen.add(w)
                merged.append(w)
                if len(merged) >= max_words:
                    break
            words = merged

    query = " ".join(words[:max_words]).strip()
    if (
        agent == "scholar"
        and query
        and "survey" not in query.lower()
        and "empirical" not in query.lower()
        and len(query.split()) < 12
    ):
        query = f"{query} empirical study"
    return query[:180] or (goal or raw)[:180]


def normalize_subquery(sub: SubQuery, goal: str) -> SubQuery:
    agent = sub.agent.value if isinstance(sub.agent, AgentName) else str(sub.agent)
    question = compact_retrieval_query(sub.question, goal=goal, agent=agent)
    return SubQuery(agent=sub.agent, question=question, rationale=sub.rationale)


def _token_set(text: str) -> set[str]:
    return {w for w in re.split(r"\W+", (text or "").lower()) if len(w) > 3}


def dedupe_subqueries(subs: list[SubQuery], *, threshold: float = 0.5) -> list[SubQuery]:
    out: list[SubQuery] = []
    seen_sets: list[set[str]] = []
    for sub in subs:
        tokens = _token_set(sub.question)
        if not tokens:
            continue
        if any(len(tokens & prev) / max(len(tokens | prev), 1) >= threshold for prev in seen_sets):
            continue
        seen_sets.append(tokens)
        out.append(sub)
    return out


def is_orchestration_topic(text: str) -> bool:
    return bool(_ORCHESTRATION_RE.search(text or ""))


def normalize_plan_subqueries(subs: list[SubQuery], query: str) -> list[SubQuery]:
    goal = user_goal(query) or query or ""
    normalized = [normalize_subquery(s, goal) for s in subs]
    return dedupe_subqueries(normalized)
