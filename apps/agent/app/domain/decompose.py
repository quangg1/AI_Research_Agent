from __future__ import annotations

import re
from typing import Any

from app.domain.schema import AgentName, SubQuery
from app.domain.textutil import (
    GOAL_META_RE,
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
        "label": "Measured numbers, benchmarks, latency, or cost",
        "trigger": r"benchmark|performance|throughput|latency|speed|cost|price|accuracy|efficien|"
        r"how much|how many|how fast|memory|overhead|utilization|flop|token",
        "keywords": [
            r"\d+\s*(?:%|x\b|ms\b|gb\b|tb\b|tokens?|requests?|hours?|seconds?|flops?)",
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
        "hint": "benchmark measured latency cost FLOP numbers evaluation",
    },
    {
        "id": "scalability",
        "label": "Scalability: KV-cache, GPU memory bandwidth, multi-node / cluster behavior",
        "trigger": r"scalab|scale[- ]?(?:out|up)|multi[- ]?node|cluster|kv[- ]?cache|hbm|"
        r"memory\s+bandwidth|distributed|horizontal\s+scal|batch\s+size",
        "keywords": [
            r"scalab",
            r"scale[- ]?(?:out|up)",
            r"multi[- ]?node",
            r"cluster",
            r"kv[- ]?cache",
            r"hbm",
            r"memory\s+bandwidth",
            r"distributed",
            r"batch\s+size",
            r"concurrent",
            r"qps",
        ],
        "hint": "scalability KV-cache GPU memory bandwidth multi-node cluster MCTS batch",
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
        "id": "worked_example",
        "label": "Concrete worked example or system walkthrough",
        "trigger": r"example|case\s+study|walkthrough|trace|token\s+flow|end[- ]to[- ]end|"
        r"deepseek|o1|o3|r1\b|vs\.?\b|versus",
        "keywords": [
            r"for example",
            r"case study",
            r"walkthrough",
            r"trace",
            r"token",
            r"step[- ]by[- ]step",
            r"concrete",
        ],
        "hint": "concrete example case study token-flow walkthrough named systems",
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

_FILLER_ASPECTS = ("mechanism", "quantitative", "constraints", "scalability")

_slot_cache: dict[str, list[dict[str, Any]]] = {}


def synthesize_dimensions_from_evidence(
    query: str,
    evidence: list[dict],
    fallback_to_heuristic: bool = True
) -> list[dict[str, Any]]:
    """Evidence-first dimension synthesis from paper concepts.
    
    Extracts technical concepts from papers and creates paper-specific
    dimensions instead of generic templates.
    
    Args:
        query: User's research question
        evidence: Scholar/search results to extract concepts from
        fallback_to_heuristic: Use heuristic dimensions if concept extraction fails
        
    Returns:
        List of dimension dicts with paper-specific concepts
    """
    from app.domain.paper_concepts import extract_paper_concepts
    from app.llm.client import llm
    
    # Extract concepts from top papers
    concepts = extract_paper_concepts(evidence, limit=5)
    
    if not concepts and fallback_to_heuristic:
        # No concepts found, fall back to heuristics
        return derive_slots(query, use_llm=True)
    
    # Group concepts by type
    methods = [c for c in concepts if c["concept_type"] == "method"]
    frameworks = [c for c in concepts if c["concept_type"] == "framework"]
    findings = [c for c in concepts if c["concept_type"] == "finding"]
    limitations = [c for c in concepts if c["concept_type"] == "limitation"]
    
    # Build dimension candidates from concepts
    dimension_candidates = []
    
    # Methods and frameworks become primary dimensions
    for concept in (methods + frameworks)[:5]:
        dim = {
            "id": _sanitize_id(concept["concept_name"]),
            "label": f"{concept['concept_name']} [{concept['cite_id']}]",
            "patterns": [
                concept["concept_name"].lower(),
                *[m["raw_text"] for m in concept.get("metrics", [])[:2]]
            ],
            "topic_terms": distinctive_terms(concept["context"], limit=5),
            "critical": True,
            "paper_cite": concept["cite_id"],
            "paper_title": concept["paper_title"],
            "example_metrics": concept.get("metrics", []),
        }
        dimension_candidates.append(dim)
    
    # If we have findings with strong metrics, add them
    for concept in findings[:3]:
        if concept.get("metrics"):
            dim = {
                "id": _sanitize_id(concept["concept_name"][:30]),
                "label": f"{concept['concept_name'][:50]}... [{concept['cite_id']}]",
                "patterns": [m["raw_text"] for m in concept["metrics"][:3]],
                "topic_terms": distinctive_terms(concept["context"], limit=5),
                "critical": False,
                "paper_cite": concept["cite_id"],
                "paper_title": concept["paper_title"],
                "example_metrics": concept["metrics"],
            }
            dimension_candidates.append(dim)
    
    # Add one limitations dimension if we have them
    if limitations:
        all_limitations = "; ".join([c["context"][:100] for c in limitations[:3]])
        dim = {
            "id": "constraints_and_limitations",
            "label": "Constraints, Limitations, and Failure Modes",
            "patterns": [
                "limitation", "constraint", "fails", "cannot", "does not",
                *[c["concept_name"] for c in limitations[:3]]
            ],
            "topic_terms": distinctive_terms(all_limitations, limit=5),
            "critical": True,
        }
        dimension_candidates.append(dim)
    
    # If we still need more dimensions, add query-driven heuristics
    if len(dimension_candidates) < 4:
        heuristic_dims = _heuristic_slots(user_goal(query) or query)
        # Only add heuristics that don't duplicate paper-specific concepts
        for h_dim in heuristic_dims:
            if not any(h_dim["id"] == d["id"] for d in dimension_candidates):
                dimension_candidates.append(h_dim)
                if len(dimension_candidates) >= 6:
                    break
    
    # Limit to 6 dimensions max
    final_dimensions = dimension_candidates[:6]

    # Never let preference/clinical/HAR-poison concepts stay critical on LoRA/FT queries.
    try:
        from app.domain.research_contract import filter_poison_must_answer_slots

        final_dimensions = filter_poison_must_answer_slots(final_dimensions, query)
    except Exception:
        pass

    # Ensure at least one is marked critical
    if final_dimensions and not any(d.get("critical") for d in final_dimensions):
        final_dimensions[0]["critical"] = True

    return final_dimensions


def _sanitize_id(name: str) -> str:
    """Convert concept name to valid dimension ID."""
    # Remove special chars, lowercase, replace spaces with underscores
    sanitized = re.sub(r'[^a-zA-Z0-9\s_-]', '', name)
    sanitized = sanitized.lower().strip().replace(' ', '_')
    # Limit length
    return sanitized[:50]


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
            "- If the question names Scalability separately from Latency/Cost, keep them as "
            "distinct dimensions (KV-cache, GPU memory bandwidth, multi-node/cluster) — "
            "do not fold scalability into latency.\n"
            "- Prefer concrete / operational dimensions over abstract survey headings.\n"
            "- 'critical' is true only when the question cannot be considered answered without it.\n"
            "- 'keywords' are 4-8 lowercase phrases that would literally appear in a source "
            "covering that dimension.\n"
            "- 'search_query' is a short web query that would find evidence for it.\n"
            "JSON: {\"dimensions\": [{\"id\": \"snake_case\", \"label\": \"...\", "
            "\"critical\": true, \"keywords\": [\"...\"], \"search_query\": \"...\"}]}"
        ),
        system=(
            "You decompose research questions into verifiable coverage dimensions. "
            "Never assume the subject area. Keep scalability distinct from latency/cost when asked. "
            "Return JSON only."
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
    # A question that names specific products/frameworks (e.g. "compare
    # LangGraph and AutoGen") needs their own documentation as evidence, not
    # a generic survey paper — otherwise the writer describes them from its
    # own training data and cites whatever survey happened to be retrieved,
    # which report_integrity's entity-citation check will later strip as
    # unverified. Seed a docs-biased query per named subject, ahead of the
    # generic slot followups below, so it survives the length cap even on a
    # tight budget.
    #
    # Root cause traced live: after briefing runs, state["query"] is
    # replaced with briefing._compose_query's blob — goal-line-1 followed by
    # "Sector:/Must cover:/Constraints:" metadata lines — and the brief's
    # own LLM-written goal line almost always PARAPHRASES AWAY the specific
    # named subjects the user asked about (a real brief turned "compare
    # OpenAI Agents, LangGraph, AutoGen, and CrewAI" into "...architectural
    # trade-offs of transitioning from single-agent to multi-agent
    # systems..."). `entity_candidates` calls `user_goal()` internally,
    # which stops at the first such metadata line, so scanning `goal` alone
    # found zero named entities even though they survived verbatim one line
    # down, e.g. "Constraints: ...Must cover representative frameworks:
    # OpenAI Agents, Anthropic Claude-based agents, LangGraph, AutoGen, and
    # CrewAI." Use goal_with_named_subjects to recover these entities.
    from app.domain.textutil import goal_with_named_subjects
    
    named = entity_candidates(goal_with_named_subjects(query), limit=16)[:8]
    for name in named:
        out.append(
            SubQuery(
                agent=AgentName.SEARCH,
                # Short and terse on purpose: normalize_plan_subqueries's
                # dedupe_subqueries drops any sub_query sharing >=50% of its
                # >3-char tokens with an earlier one. A shared 4-word suffix
                # like "official documentation architecture features" made
                # every entity's query a near-duplicate of the last (real
                # bug: only the first-listed entity ever survived planning,
                # regardless of how many were seeded here) — one shared
                # token ("docs") keeps distinct names well under that
                # threshold while still reading as a real search query.
                question=f"{name} docs",
                rationale=f"Fetch official documentation for the named subject: {name}",
            )
        )
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
    # The direct-answer entry plus every seeded entity-docs query must
    # survive this cap — a 5-framework comparison question needs all 5, not
    # whichever ones happened to fit before the generic dimension-followup
    # cap kicked in (real bug: direct(1) + 6 entities = 7 raw entries got
    # sliced to 6, always dropping the last-listed subjects, e.g. AutoGen
    # and CrewAI, while the earlier-listed OpenAI/Agents/Anthropic survived
    # every time regardless of remaining_calls). Only the generic slot
    # followups after them are subject to the tighter budget-based cap.
    guaranteed = 1 + len(named)
    return unique[: max(max_n, guaranteed)]


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
