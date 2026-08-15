from __future__ import annotations

import re
from typing import Any

from app.domain.schema import AgentName, SubQuery
from app.domain.textutil import (
    content_terms,
    distinctive_terms,
    entity_candidates,
    entity_pattern,
    user_goal,
)

# Generic research aspects. These describe *shapes of questions*, not any subject
# matter, so the same catalogue works for kernels, biology, law, or pricing.
ASPECTS: list[dict[str, Any]] = [
    {
        "id": "direct_answer",
        "label": "Direct answer to the question as asked",
        "always": True,
        "critical": True,
        "keywords": [],
        "hint": "",
    },
    {
        "id": "mechanism",
        "label": "How it works, step by step",
        "trigger": r"\bhow\b|\bwhy\b|mechanism|internal|architect|pipeline|workflow|algorithm|process|"
        r"implement|design|under the hood|explain",
        "keywords": [
            r"how it works",
            r"mechanism",
            r"architect",
            r"pipeline",
            r"algorithm",
            r"step",
            r"stage",
            r"process",
            r"implement",
            r"design",
            r"consists? of",
            r"works by",
        ],
        "hint": "how it works architecture step by step explanation",
    },
    {
        "id": "implementation",
        "label": "Implementation or source-level evidence",
        "trigger": r"\bcode\b|source|repo|repositor|library|framework|kernel|api|sdk|implement|package|"
        r"module|driver|config|open-?source",
        "keywords": [
            r"github",
            r"gitlab",
            r"repositor",
            r"source code",
            r"implement",
            r"library",
            r"module",
            r"function",
            r"class\b",
            r"readme",
            r"api\b",
            r"reference implementation",
        ],
        "hint": "official repository source code implementation reference",
    },
    {
        "id": "comparison",
        "label": "Differences between the named options",
        "trigger": r"\bvs\.?\b|versus|compare|comparison|difference|better|instead of|trade-?off|"
        r"alternative|which one",
        "keywords": [
            r"compared",
            r"versus",
            r"unlike",
            r"whereas",
            r"difference",
            r"outperform",
            r"baseline",
            r"in contrast",
            r"relative to",
        ],
        "hint": "comparison differences trade-offs between options",
    },
    {
        "id": "quantitative",
        "label": "Measured numbers, benchmarks, or costs",
        "trigger": r"benchmark|performance|throughput|latency|speed|cost|price|accuracy|efficien|"
        r"how much|how many|how fast|scal|memory|overhead|utilization",
        "keywords": [
            r"\d+\s*(?:%|x\b|ms\b|gb\b|tb\b|tokens?|requests?|hours?|seconds?)",
            r"benchmark",
            r"throughput",
            r"latency",
            r"speedup",
            r"accuracy",
            r"cost",
            r"measured",
            r"evaluat",
            r"results? show",
        ],
        "hint": "benchmark measured results numbers evaluation",
    },
    {
        "id": "constraints",
        "label": "Limits, bottlenecks, and failure modes",
        "trigger": r"limit|bottleneck|constraint|trade-?off|drawback|risk|fail|problem|challenge|"
        r"overhead|downside|caveat|when not to",
        "keywords": [
            r"limitation",
            r"bottleneck",
            r"overhead",
            r"trade-?off",
            r"drawback",
            r"constraint",
            r"fails?\b",
            r"degrad",
            r"challeng",
            r"does not",
            r"cannot",
        ],
        "hint": "limitations bottlenecks failure modes trade-offs",
    },
    {
        "id": "procedure",
        "label": "Concrete steps, settings, or usage",
        "trigger": r"how (?:to|do i|can i)|setup|set up|install|configur|deploy|enable|integrat|"
        r"tutorial|guide|steps",
        "keywords": [
            r"install",
            r"configur",
            r"deploy",
            r"enable",
            r"command",
            r"flag\b",
            r"parameter",
            r"usage",
            r"example",
            r"tutorial",
            r"step \d",
        ],
        "hint": "setup configuration usage steps example",
    },
    {
        "id": "recency",
        "label": "Current state and recent changes",
        "trigger": r"latest|current|recent|today|now\b|2025|2026|state of the art|sota|up to date|"
        r"still\b|modern",
        "keywords": [
            r"\b20(?:2[4-9]|3\d)\b",
            r"release",
            r"latest",
            r"current",
            r"updated",
            r"version",
            r"changelog",
            r"announc",
            r"deprecat",
        ],
        "hint": "latest release current version changes",
    },
    {
        "id": "caveats",
        "label": "Contradictions and open questions",
        "always": True,
        "critical": False,
        "keywords": [
            r"however",
            r"contrary",
            r"disagree",
            r"unclear",
            r"open question",
            r"future work",
            r"remains?\b",
            r"debate",
        ],
        "hint": "contradictions open questions unresolved",
    },
]

_FILLER_ASPECTS = ("mechanism", "quantitative", "constraints")

_slot_cache: dict[str, list[dict[str, Any]]] = {}


def derive_slots(query: str, use_llm: bool = True) -> list[dict[str, Any]]:
    """Must-answer dimensions derived from the question itself.

    No subject-matter knowledge is baked in: an LLM decomposes the question when
    available, otherwise generic question-shape heuristics are used.
    """
    goal = user_goal(query) or (query or "")
    key = goal.strip().lower()
    if not key:
        return []
    if key in _slot_cache:
        return [dict(s) for s in _slot_cache[key]]
    slots = (_llm_slots(goal) if use_llm else None) or _heuristic_slots(goal)
    _slot_cache[key] = [dict(s) for s in slots]
    return [dict(s) for s in slots]


def reset_slot_cache() -> None:
    _slot_cache.clear()


def _heuristic_slots(goal: str) -> list[dict[str, Any]]:
    topic = distinctive_terms(goal, limit=8) or content_terms(goal, limit=8)
    entities = entity_candidates(goal, limit=6)
    chosen: list[dict[str, Any]] = []
    for aspect in ASPECTS:
        asked = bool(aspect.get("trigger") and re.search(aspect["trigger"], goal, re.I))
        if not (aspect.get("always") or asked):
            continue
        if aspect["id"] == "comparison" and len(entities) < 2 and not asked:
            continue
        chosen.append(_slot_from_aspect(aspect, goal, topic, entities, critical=asked))
    if len(chosen) < 4:
        for aspect_id in _FILLER_ASPECTS:
            if any(s["id"] == aspect_id for s in chosen):
                continue
            aspect = next(a for a in ASPECTS if a["id"] == aspect_id)
            chosen.append(_slot_from_aspect(aspect, goal, topic, entities, critical=False))
            if len(chosen) >= 4:
                break
    return chosen


def _slot_from_aspect(
    aspect: dict[str, Any],
    goal: str,
    topic: list[str],
    entities: list[str],
    critical: bool,
) -> dict[str, Any]:
    critical = bool(aspect.get("critical")) or critical
    hint = aspect.get("hint") or ""
    subject = " ".join(entities[:3]) or " ".join(topic[:4]) or goal[:80]
    followup = f"{subject} {hint}".strip() or goal
    return {
        "id": aspect["id"],
        "label": aspect["label"],
        "status": "open",
        "evidence_ids": [],
        "critical": critical,
        "priority": "critical" if critical else "standard",
        "patterns": list(aspect.get("keywords") or []),
        "topic_terms": topic[:8],
        "followup": followup,
        "aspect": aspect["id"],
    }


def _llm_slots(goal: str) -> list[dict[str, Any]] | None:
    from app.llm.client import llm

    if not llm.available:
        return None
    payload = llm.generate_json(
        prompt=(
            f"Question:\n{goal}\n\n"
            "Decompose this question into the dimensions an answer MUST cover to be complete.\n"
            "Work for any subject. Do not assume a domain.\n"
            "Rules:\n"
            "- 4 to 8 dimensions, ordered from most to least central.\n"
            "- Each dimension is something evidence could confirm or fail to confirm.\n"
            "- 'critical' is true only when the question cannot be considered answered without it.\n"
            "- 'keywords' are 4-8 lowercase phrases that would literally appear in a source "
            "covering that dimension.\n"
            "- 'search_query' is a short web query that would find evidence for it.\n"
            "JSON: {\"dimensions\": [{\"id\": \"snake_case\", \"label\": \"...\", "
            "\"critical\": true, \"keywords\": [\"...\"], \"search_query\": \"...\"}]}"
        ),
        system=(
            "You decompose research questions into verifiable coverage dimensions. "
            "Never assume the subject area. Return JSON only."
        ),
        max_tokens=1600,
    )
    rows = (payload or {}).get("dimensions") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    topic = distinctive_terms(goal, limit=8) or content_terms(goal, limit=8)
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        sid = re.sub(r"[^a-z0-9_]+", "_", str(row.get("id") or label).lower()).strip("_")[:40]
        if not sid or sid in seen:
            continue
        seen.add(sid)
        keywords = [
            str(k).strip().lower()
            for k in (row.get("keywords") or [])
            if isinstance(k, (str, int, float)) and str(k).strip()
        ]
        critical = bool(row.get("critical"))
        slots.append(
            {
                "id": sid,
                "label": label[:120],
                "status": "open",
                "evidence_ids": [],
                "critical": critical,
                "priority": "critical" if critical else "standard",
                "patterns": [re.escape(k) for k in keywords[:8]],
                "topic_terms": topic[:8],
                "followup": str(row.get("search_query") or f"{goal} {label}")[:200],
                "aspect": sid,
            }
        )
    if not slots:
        return None
    if not any(s["critical"] for s in slots):
        slots[0]["critical"] = True
        slots[0]["priority"] = "critical"
    return slots


def must_cover_from_slots(slots: list[dict[str, Any]]) -> list[str]:
    out = [str(s.get("label")) for s in slots if s.get("label")]
    return out or ["Answer the question directly with cited evidence"]


def subquestions_for(query: str, remaining_calls: int = 8) -> list[SubQuery]:
    """Claim-seeking sub-queries built from the question's own dimensions."""
    goal = user_goal(query) or query or ""
    slots = derive_slots(query)
    out: list[SubQuery] = [
        SubQuery(
            agent=AgentName.SEARCH,
            question=goal,
            rationale="Direct evidence for the question as asked.",
        )
    ]
    for slot in slots:
        if slot["id"] == "direct_answer":
            continue
        question = (slot.get("followup") or "").strip()
        if not question or question == goal:
            continue
        out.append(
            SubQuery(
                agent=_agent_for_slot(slot),
                question=question[:200],
                rationale=f"Cover must-answer dimension: {slot.get('label')}",
            )
        )
    seen: set[str] = set()
    unique: list[SubQuery] = []
    for sub in out:
        key = sub.question.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(sub)
    max_n = max(2, min(6, remaining_calls + 1))
    return unique[:max_n]


def _agent_for_slot(slot: dict[str, Any]) -> AgentName:
    text = f"{slot.get('id')} {slot.get('label')} {slot.get('followup')}".lower()
    if re.search(r"paper|study|research|peer|academic|arxiv|literature|benchmark|evaluat", text):
        return AgentName.SCHOLAR
    return AgentName.SEARCH


def rewrite_gap_query(
    query: str,
    gap: dict[str, Any],
    *,
    use_llm: bool = False,
) -> tuple[str, AgentName]:
    """Turn one critic coverage gap into a topic-preserving retrieval query."""
    goal = user_goal(query) or query or ""
    entities = entity_candidates(goal, limit=5)
    topics = distinctive_terms(goal, limit=8) or content_terms(goal, limit=8)
    gap_text = " ".join(
        str(gap.get(key) or "").strip()
        for key in ("label", "aspect", "followup")
        if gap.get(key)
    )
    subject = " ".join(entities) or " ".join(topics[:5])
    intent = str(gap.get("label") or gap.get("aspect") or "direct evidence").strip()
    fallback = re.sub(r"\s+", " ", f"{subject} {intent} {gap_text}".strip())[:200]
    agent = _agent_for_gap(gap_text)
    if not use_llm:
        return fallback or goal[:200], agent

    rewritten = _llm_gap_query(goal, gap_text, entities)
    if rewritten:
        candidate, suggested_agent = rewritten
        # Never accept a rewrite that drops a named target from the question.
        if all(re.search(entity_pattern(entity), candidate, re.I) for entity in entities):
            return candidate[:200], suggested_agent
    return fallback or goal[:200], agent


def _agent_for_gap(text: str) -> AgentName:
    if re.search(
        r"\b(paper|study|peer|academic|literature|benchmark|experiment|evaluation|"
        r"accuracy|measured|empirical|state of the art)\b",
        text or "",
        re.I,
    ):
        return AgentName.SCHOLAR
    return AgentName.SEARCH


def _llm_gap_query(goal: str, gap_text: str, entities: list[str]) -> tuple[str, AgentName] | None:
    from app.llm.client import llm

    if not llm.available:
        return None
    payload = llm.generate_json(
        prompt=(
            f"Original research question: {goal}\nCoverage gap: {gap_text}\n"
            f"Named targets that must remain verbatim: {entities}\n"
            "Write one concise query that finds direct evidence for only this gap. "
            'Choose source="scholar" for papers/empirical studies, otherwise source="search". '
            'JSON: {"query":"...","source":"search|scholar"}.'
        ),
        system="You rewrite critic coverage gaps into precise retrieval queries. Return JSON only.",
        max_tokens=400,
    )
    if not isinstance(payload, dict) or not str(payload.get("query") or "").strip():
        return None
    source = AgentName.SCHOLAR if payload.get("source") == "scholar" else AgentName.SEARCH
    return str(payload["query"]).strip(), source
