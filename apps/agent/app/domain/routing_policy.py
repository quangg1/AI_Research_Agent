from __future__ import annotations

import re

from app.domain.research_intent import (
    decompose_subquestions,
    is_mechanism_query,
    user_goal,
)
from app.domain.schema import AgentName, Plan, QueryType, SubQuery

LEARN_RE = re.compile(
    r"\b(learn(?:ing)? path|how to learn|curriculum|mastering|study plan|roadmap|"
    r"high-yield|hands-on milestone|course for|optimized.{0,40}learning)\b",
    re.I,
)
COMPARE_RE = re.compile(
    r"\b(vs\.?|versus|compare|comparison|or instead|trade-?off|better than)\b",
    re.I,
)
OPEN_RE = re.compile(
    r"\b(should|strategy|invest|decide|recommend|option|pathway|playbook|how should|when to)\b",
    re.I,
)
FACTUAL_RE = re.compile(
    r"\b(what is|when (does|did|is)|threshold|deadline|definition|does rag|does a vector|can unbundled|how does vllm|what does)\b",
    re.I,
)
LEAD_FACTUAL_RE = re.compile(r"^(what|when|who|which|does|do|can|is|are|if)\b", re.I)

OUT_OF_SCOPE_RE = re.compile(
    r"\b(nestjs chatbot|kaggle sentiment|leetcode|resume rewrite|crypto trading|diet plan|medical diagnosis)\b",
    re.I,
)


def is_learning_query(query: str) -> bool:
    return bool(LEARN_RE.search(user_goal(query) or query or ""))


def classify_query(query: str) -> QueryType:
    goal = user_goal(query) or query
    if is_learning_query(goal):
        return QueryType.OPEN_RESEARCH
    if is_mechanism_query(goal):
        return QueryType.FACTUAL
    if COMPARE_RE.search(goal):
        return QueryType.COMPARISON
    if OPEN_RE.search(goal):
        return QueryType.OPEN_RESEARCH
    if FACTUAL_RE.search(goal):
        return QueryType.FACTUAL
    lead = LEAD_FACTUAL_RE.search(goal.strip())
    if lead and not re.search(r"\bif\b", goal.strip()[lead.end() :], re.I):
        return QueryType.FACTUAL
    if len(goal.split()) <= 12:
        return QueryType.FACTUAL
    return QueryType.OPEN_RESEARCH


def agents_for(
    query_type: QueryType,
    remaining_calls: int,
    has_corpus: bool = True,
    live_first: bool = False,
) -> list[AgentName]:
    if live_first:
        if remaining_calls <= 1:
            return [AgentName.SEARCH]
        return [AgentName.SEARCH, AgentName.SCHOLAR][: max(1, remaining_calls)]
    if remaining_calls <= 1:
        return [AgentName.DOCS] if has_corpus else [AgentName.SEARCH]
    if query_type == QueryType.FACTUAL:
        # Always include SCHOLAR for better quality, even on factual queries
        chosen = [AgentName.DOCS, AgentName.SEARCH, AgentName.SCHOLAR]
    elif query_type == QueryType.COMPARISON:
        chosen = [AgentName.DOCS, AgentName.SEARCH, AgentName.SCHOLAR]
    else:
        chosen = [AgentName.SEARCH, AgentName.SCHOLAR, AgentName.DOCS]
    if not has_corpus:
        chosen = [a for a in chosen if a != AgentName.DOCS]
        if AgentName.SEARCH not in chosen:
            chosen = [AgentName.SEARCH, *chosen]
        if not chosen:
            chosen = [AgentName.SEARCH]
    return chosen[: max(1, remaining_calls)]


def heuristic_plan(
    query: str,
    remaining_calls: int,
    followups: list[SubQuery] | None = None,
    has_corpus: bool = True,
    live_first: bool | None = None,
) -> Plan:
    goal = user_goal(query) or query
    qtype = classify_query(goal)
    if live_first is None:
        live_first = is_learning_query(goal)
    agents = agents_for(qtype, remaining_calls, has_corpus=has_corpus, live_first=live_first)
    sub: list[SubQuery] = list(followups or [])
    if not sub:
        if is_mechanism_query(goal) or live_first:
            if is_mechanism_query(goal):
                agents = [AgentName.SEARCH, AgentName.SCHOLAR][: max(1, remaining_calls)]
                live_first = True
            decomposed = decompose_subquestions(goal, remaining_calls=remaining_calls)
            allowed = set(agents)
            sub = [s for s in decomposed if s.agent in allowed] or decomposed[: len(agents)]
        else:
            templates = {
                AgentName.DOCS: SubQuery(
                    agent=AgentName.DOCS,
                    question=goal,
                    rationale="Primary docs, framework guides, and curated systems notes.",
                ),
                AgentName.SEARCH: SubQuery(
                    agent=AgentName.SEARCH,
                    question=goal,
                    rationale="Live vendor docs, papers, and engineering writeups for the asked artifacts.",
                ),
                AgentName.SCHOLAR: SubQuery(
                    agent=AgentName.SCHOLAR,
                    question=goal,
                    rationale="Peer-reviewed and preprint works that mention the asked artifacts.",
                ),
            }
            for agent in agents:
                if agent in templates:
                    sub.append(templates[agent])
    agents_from_sub = list(dict.fromkeys(s.agent for s in sub)) or agents
    return Plan(
        query_type=qtype,
        goal=goal,
        sub_queries=sub,
        agents_to_run=agents_from_sub[: max(1, remaining_calls)],
        assumptions=[
            "Domain is applied AI / LLM systems (serving, RAG, agents, eval, multi-LoRA kernels).",
            "Vendor blogs are context, not ground truth, unless corroborated by primary docs, papers, or official repos.",
        ]
        + (
            ["Local corpus is empty; live search and scholar must supply every citation URL."]
            if not has_corpus
            else (
                ["Local corpus does not cover this question; cite live search and scholar URLs, not internal notes."]
                if live_first
                else []
            )
        )
        + (
            ["Prefer paper + GitHub/CUDA evidence for mechanism claims; demote secondary blogs."]
            if is_mechanism_query(goal)
            else []
        ),
        stop_conditions=[
            "At least one primary-docs, eval-lab, peer-reviewed, or official-repo source for architectural claims.",
            "Contradictions on latency, cost, eval, or kernel limitations are surfaced, not averaged away.",
        ],
    )


def out_of_scope(query: str) -> bool:
    return bool(OUT_OF_SCOPE_RE.search(user_goal(query) or query))
