from __future__ import annotations

"""ResearchContract: compile query+brief into enforceable research scope.

Phase A of Kiln memo-quality. Pure functions only — no graph/psycopg imports.
Attach as brief["research_contract"] (dict) after the brief exists.
"""


import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.domain.metric_grounding import (
    assess_subject_scale_scope,
    assess_subject_topic_scope,
    extract_model_scales,
    is_memory_footprint_query,
    source_topic_tags,
)

# Canonical primaries for LoRA / QLoRA VRAM questions.
MANDATORY_LORA_QLORA_SOURCES: list[dict[str, str]] = [
    {
        "arxiv_id": "2106.09685",
        "label": "Hu et al. LoRA",
        "role": "lora_primary",
        "url_hint": "arxiv.org/abs/2106.09685",
    },
    {
        "arxiv_id": "2305.14314",
        "label": "Dettmers et al. QLoRA",
        "role": "qlora_primary",
        "url_hint": "arxiv.org/abs/2305.14314",
    },
]

# Domains that must not drive memory/VRAM LoRA-vs-QLoRA answers.
DEFAULT_EXCLUDED_FOR_MEMORY = (
    "DPO",
    "ORPO",
    "KTO",
    "RLHF",
    "clinical",
    "mental-health",
    "email-QA",
    "abstention",
)

AUTHORITY_POLICY_DEFAULT: dict[str, Any] = {
    "prefer": [
        "primary_papers",
        "official_repos",
        "measured_memory_evidence",
    ],
    "repos": [
        "microsoft/LoRA",
        "artidoro/qlora",
        "TimDettmers/bitsandbytes",
    ],
    "authors": [
        "Edward J. Hu",
        "Tim Dettmers",
    ],
    "demote": [
        "secondary_blogs",
        "preference_alignment_papers_without_vram",
    ],
}

LORA_QLORA_RE = re.compile(r"\b(?:LoRA|QLoRA|QLORA)\b", re.I)
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")


@dataclass
class ResearchContract:
    """Compiled, enforceable research scope for one run."""

    must_cover: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    scale_bounds: list[str] = field(default_factory=list)
    mandatory_sources: list[dict[str, str]] = field(default_factory=list)
    authority_policy: dict[str, Any] = field(default_factory=dict)
    raw_constraints: list[str] = field(default_factory=list)
    query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ResearchContract | None:
        if not data or not isinstance(data, dict):
            return None
        return cls(
            must_cover=list(data.get("must_cover") or []),
            excluded_domains=list(data.get("excluded_domains") or []),
            scale_bounds=list(data.get("scale_bounds") or []),
            mandatory_sources=[dict(x) for x in (data.get("mandatory_sources") or []) if isinstance(x, dict)],
            authority_policy=dict(data.get("authority_policy") or {}),
            raw_constraints=list(data.get("raw_constraints") or []),
            query=str(data.get("query") or ""),
        )


def compile_research_contract(query: str, brief: dict[str, Any] | None = None) -> ResearchContract:
    """Compile a ResearchContract from the user query and (optional) brief dict."""
    brief = brief if isinstance(brief, dict) else {}
    q = (query or brief.get("goal") or "").strip()

    must = [str(x) for x in (brief.get("must_cover") or []) if str(x).strip()]
    if not must:
        try:
            from app.domain.research_intent import must_cover_for

            must = list(must_cover_for(q) or [])
        except Exception:
            must = []

    raw_constraints: list[str] = []
    for key in ("constraints", "out_of_scope"):
        for item in brief.get(key) or []:
            text = str(item).strip()
            if text and text not in raw_constraints:
                raw_constraints.append(text)

    scale_bounds = sorted(extract_model_scales(q))
    excluded: list[str] = []
    mandatory: list[dict[str, str]] = []
    authority = dict(AUTHORITY_POLICY_DEFAULT)

    memory_q = is_memory_footprint_query(q) or (
        LORA_QLORA_RE.search(q) is not None
        and re.search(r"\b(?:VRAM|memory|footprint|GB)\b", q, re.I) is not None
    )
    if memory_q or LORA_QLORA_RE.search(q):
        excluded = list(DEFAULT_EXCLUDED_FOR_MEMORY)
        # Always require Hu / Dettmers for LoRA/QLoRA VRAM or FT tradeoff comparisons.
        if memory_q or re.search(r"\b(?:LoRA|QLoRA).{0,40}(?:QLoRA|LoRA)\b", q, re.I) or re.search(
            r"\b(?:full[\s-]?(?:FT|fine)|fine[\s-]?tun).{0,80}\b(?:LoRA|QLoRA)\b|"
            r"\b(?:LoRA|QLoRA).{0,80}\b(?:full[\s-]?(?:FT|fine)|fine[\s-]?tun|accuracy|OOD|trade-?off)\b",
            q,
            re.I,
        ):
            mandatory = [dict(x) for x in MANDATORY_LORA_QLORA_SOURCES]
        if scale_bounds:
            raw_constraints.append(
                f"Keep quantitative attributions scoped to measured scales: {', '.join(scale_bounds)}"
            )
        raw_constraints.append(
            "Exclude preference (DPO/ORPO/KTO), clinical, and email-QA/abstention papers "
            "as drivers of VRAM/memory findings"
        )
        if "2106.09685" not in " ".join(raw_constraints):
            raw_constraints.append(
                "Mandatory primaries when claiming LoRA/QLoRA VRAM: Hu 2106.09685, Dettmers 2305.14314"
            )

    # Honour brief out_of_scope tokens that look like domain excludes.
    for item in brief.get("out_of_scope") or []:
        low = str(item).lower()
        for token in ("dpo", "orpo", "kto", "clinical", "email-qa", "abstention", "rlhf"):
            if token in low and token.upper() not in {e.upper() for e in excluded}:
                excluded.append(token.upper() if token in {"dpo", "orpo", "kto", "rlhf"} else token)

    return ResearchContract(
        must_cover=must,
        excluded_domains=excluded,
        scale_bounds=scale_bounds,
        mandatory_sources=mandatory,
        authority_policy=authority,
        raw_constraints=raw_constraints,
        query=q,
    )


def topic_relevance_score(ev: dict, query: str = "") -> float:
    """Continuous topic relevance in [0, 1] for relevance-first ranking.

    0.0 = shares no distinctive terms with the question (unless benchmark signal).
    Higher = more distinctive-term overlap. Credibility/tier belong elsewhere.
    """
    from app.domain.research_intent import (
        _shares_no_distinctive_term,
        distinctive_terms,
        user_goal,
    )

    # Local copy — avoid importing adversarial (circular with retrieval_rank_score).
    benchmark_re = re.compile(
        r"\b(swe-bench|humaneval|mmlu|gsm8k|hellaswag|arc-challenge|winogrande|"
        r"truthfulqa|bbh|mt-bench|arena|livecodebench)\b",
        re.I,
    )

    from app.domain.offtopic_domains import evidence_is_har_sensor_ood

    if evidence_is_har_sensor_ood(ev, query):
        return 0.0

    goal = user_goal(query) if query else ""
    anchors = set(distinctive_terms(goal, limit=10)) if goal else set()
    blob = " ".join(str(ev.get(k) or "") for k in ("title", "snippet", "quote", "full_text"))
    if not anchors:
        # No anchors -> neutral relevance so authority can still order.
        return 0.5
    if _shares_no_distinctive_term(ev, anchors):
        if benchmark_re.search(blob):
            return 0.35  # weak keep for named-benchmark fragments
        return 0.0
    low = blob.lower()
    hits = sum(1 for a in anchors if a in low)
    return max(0.15, min(1.0, hits / max(3, min(len(anchors), 6))))


def evidence_violates_excludes(ev: dict, contract: ResearchContract | dict | None) -> bool:
    """True when evidence is dominated by contract-excluded domains (no memory evidence)."""
    c = contract if isinstance(contract, ResearchContract) else ResearchContract.from_dict(contract)  # type: ignore[arg-type]
    if not c or not c.excluded_domains:
        return False
    blob = " ".join(str(ev.get(k) or "") for k in ("title", "snippet", "quote", "full_text"))
    if len(blob) < 40:
        return False
    tags = source_topic_tags(blob)
    off = tags & {"preference", "clinical", "abstention"}
    if not off:
        # Also match explicit exclude tokens in title/snippet.
        title_snip = f"{ev.get('title') or ''} {ev.get('snippet') or ''}"
        for dom in c.excluded_domains:
            if re.search(rf"\b{re.escape(dom)}\b", title_snip, re.I):
                # Only exclude if no on-topic memory evidence for memory contracts.
                if c.scale_bounds or is_memory_footprint_query(c.query):
                    if "memory" not in tags:
                        return True
                else:
                    return True
        return False
    if "memory" in tags and is_memory_footprint_query(c.query):
        return False
    return True


def evidence_fails_scope_assessors(
    ev: dict,
    query: str,
    contract: ResearchContract | dict | None = None,
) -> bool:
    """Pre-cite scope check using metric_grounding assessors + contract excludes.

    Returns True when the evidence should NOT enter the citation ledger / dossier
    as a load-bearing source for this query.
    """
    c = contract if isinstance(contract, ResearchContract) else ResearchContract.from_dict(
        contract if isinstance(contract, dict) else None
    )
    q = query or (c.query if c else "")
    if evidence_violates_excludes(ev, c):
        return True

    blob = " ".join(str(ev.get(k) or "") for k in ("title", "snippet", "quote", "full_text"))
    if len(blob) < 40:
        return False

    # Treat title+snippet as a pseudo-claim about the asked subject.
    claim = f"{ev.get('title') or ''} {ev.get('snippet') or ''}".strip()
    if not claim:
        claim = blob[:400]

    topic = assess_subject_topic_scope(claim, blob, query=q)
    if topic and topic.get("status") == "topic_mismatch":
        return True

    # Scale: if contract/query asks for a scale and source only has other scales,
    # and source has no overlap with asked scales → fail for memory queries.
    asked = set((c.scale_bounds if c else []) or extract_model_scales(q))
    source_scales = extract_model_scales(blob)
    if asked and source_scales and not (asked & source_scales):
        if is_memory_footprint_query(q):
            # Soft: only drop when source looks like a quantitative memory paper
            # for the wrong scale (classic 1.5B bleed).
            scale = assess_subject_scale_scope(
                f"For {next(iter(asked))} fine-tuning peak VRAM is measured.",
                blob,
                query=q,
            )
            if scale and scale.get("status") == "scale_mismatch":
                return True
    return False


def filter_evidence_for_contract(
    evidence: list[dict],
    query: str,
    contract: ResearchContract | dict | None = None,
    *,
    mark_only: bool = False,
) -> list[dict]:
    """Drop (or mark) evidence that fails pre-cite contract scope.

    mark_only=True sets ev['contract_excluded']=True but keeps the row for critic fallback.
    """
    c = contract if isinstance(contract, ResearchContract) else ResearchContract.from_dict(
        contract if isinstance(contract, dict) else None
    )
    if not c and not query:
        return list(evidence or [])
    out: list[dict] = []
    for ev in evidence or []:
        if evidence_fails_scope_assessors(ev, query, c):
            if mark_only:
                row = dict(ev)
                row["contract_excluded"] = True
                row["off_topic"] = True
                out.append(row)
            continue
        out.append(ev)
    return out if out else list(evidence or [])


def contract_from_brief_or_state(
    brief: dict | None = None,
    state: dict | None = None,
    query: str = "",
) -> ResearchContract | None:
    """Resolve a contract from brief dict, state key, or compile fresh (fail-soft)."""
    brief = brief or {}
    state = state or {}
    raw = brief.get("research_contract") or state.get("research_contract")
    if raw:
        return ResearchContract.from_dict(raw)
    q = query or state.get("query") or brief.get("goal") or ""
    if not q:
        return None
    try:
        return compile_research_contract(q, brief)
    except Exception:
        return None


def mandatory_sources_present(
    contract: ResearchContract | dict | None,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> list[dict[str, str]]:
    """Return mandatory_sources entries that are missing from cites/evidence."""
    c = contract if isinstance(contract, ResearchContract) else ResearchContract.from_dict(
        contract if isinstance(contract, dict) else None
    )
    if not c or not c.mandatory_sources:
        return []
    blob_parts: list[str] = []
    for row in list(citations or []) + list(evidence or []):
        blob_parts.append(
            " ".join(
                str(row.get(k) or "")
                for k in ("url", "title", "snippet", "quote", "arxiv_id", "id")
            )
        )
    hay = " ".join(blob_parts).lower()
    missing: list[dict[str, str]] = []
    for src in c.mandatory_sources:
        aid = (src.get("arxiv_id") or "").lower()
        hint = (src.get("url_hint") or "").lower()
        label = (src.get("label") or "").lower()
        found = False
        if aid and aid in hay:
            found = True
        if hint and hint in hay:
            found = True
        # Author token heuristic (dettmers / hu et al.)
        if "dettmers" in label and "dettmers" in hay:
            found = True
        if "hu" in label and re.search(r"\bhu\b.{0,20}\blora\b|\blora\b.{0,20}\bhu\b", hay):
            found = True
        if not found:
            missing.append(dict(src))
    return missing


# --- Contract-aligned must-answer (LoRA / QLoRA / FT tradeoff queries) --------

POISON_CRITICAL_SLOT_RE = re.compile(
    r"preference(?:[-_ ]based)?|class[-_ ]?rebalanc|self[-_ ]?supervised|"
    r"clinical|abstention|email[-_ ]?qa|\bdpo\b|\borpo\b|\bkto\b|\brlhf\b|"
    r"mental[-_ ]health",
    re.I,
)

FT_TRADEOFF_SIGNAL_RE = re.compile(
    r"\b(?:full[\s-]?(?:FT|fine[\s-]?tun(?:ing|e)?)|fine[\s-]?tun(?:ing|e)?|"
    r"VRAM|memory|footprint|accuracy|trade-?offs?|OOD|cost|PEFT|"
    r"catastrophic\s+forget|quantiz)\b",
    re.I,
)

# Canonical dimensions for LoRA/QLoRA/FT empirical tradeoff questions.
# Aligned to typical must_cover; never preference/clinical/abstention.
LORA_FT_MUST_ANSWER_SPECS: list[dict[str, Any]] = [
    {
        "id": "task_accuracy",
        "label": "Task accuracy versus full fine-tuning",
        "critical": True,
        "patterns": [
            r"accurac",
            r"benchmark",
            r"perplexity",
            r"downstream",
            r"MMLU",
            r"evaluat",
            r"full\s+fine[- ]?tun",
        ],
        "hint": "task accuracy full fine-tuning vs LoRA QLoRA",
    },
    {
        "id": "vram_memory",
        "label": "VRAM / peak training memory",
        "critical": True,
        "patterns": [
            r"VRAM",
            r"peak\s+(?:training\s+)?mem",
            r"memory\s+footprint",
            r"\d+(?:\.\d+)?\s*GB",
            r"GPU\s+mem",
        ],
        "hint": "peak VRAM memory footprint LoRA QLoRA",
    },
    {
        "id": "compute_cost",
        "label": "Compute cost and training efficiency",
        "critical": True,
        "patterns": [
            r"cost",
            r"FLOP",
            r"throughput",
            r"tokens?/s",
            r"training\s+time",
            r"efficien",
            r"wall[- ]?clock",
        ],
        "hint": "compute cost training time efficiency LoRA QLoRA",
    },
    {
        "id": "ood_generalization",
        "label": "OOD generalization / catastrophic forgetting",
        "critical": True,
        "patterns": [
            r"\bOOD\b",
            r"out[- ]of[- ]distribution",
            r"catastrophic\s+forget",
            r"forget",
            r"generaliz",
            r"retention",
        ],
        "hint": "OOD generalization catastrophic forgetting LoRA",
    },
    {
        "id": "quantization_tradeoffs",
        "label": "Quantization tradeoffs (QLoRA / NF4 / precision)",
        "critical": True,
        "patterns": [
            r"quantiz",
            r"NF4",
            r"4[- ]?bit",
            r"QLoRA",
            r"precision",
            r"bitsandbytes",
            r"double\s+quant",
        ],
        "hint": "QLoRA NF4 quantization precision tradeoffs",
    },
    {
        "id": "constraints_and_limitations",
        "label": "Constraints and limitations",
        "critical": False,
        "patterns": [
            r"limitation",
            r"constraint",
            r"trade-?off",
            r"drawback",
            r"bottleneck",
            r"fails?\b",
        ],
        "hint": "limitations constraints LoRA QLoRA fine-tuning",
    },
]


def is_lora_qlora_ft_query(query: str) -> bool:
    """True for LoRA/QLoRA/FT tradeoff or VRAM/accuracy comparison questions."""
    q = (query or "").strip()
    if not q or not LORA_QLORA_RE.search(q):
        return False
    return FT_TRADEOFF_SIGNAL_RE.search(q) is not None


def is_poison_critical_slot(slot_id: str = "", label: str = "") -> bool:
    """Preference/clinical/abstention-style slots must never be critical for FT queries."""
    blob = f"{slot_id or ''} {label or ''}"
    return bool(POISON_CRITICAL_SLOT_RE.search(blob))


def _slot_from_spec(spec: dict[str, Any], topic: list[str], goal: str) -> dict[str, Any]:
    critical = bool(spec.get("critical"))
    hint = str(spec.get("hint") or "")
    followup = f"{goal[:120]} {hint}".strip()[:200]
    return {
        "id": spec["id"],
        "label": spec["label"],
        "status": "open",
        "evidence_ids": [],
        "critical": critical,
        "priority": "critical" if critical else "standard",
        "patterns": list(spec.get("patterns") or []),
        "topic_terms": topic[:8],
        "followup": followup,
        "aspect": spec["id"],
        "from_contract": True,
    }


def must_answer_from_contract(
    query: str,
    contract: ResearchContract | dict | None = None,
    *,
    brief: dict | None = None,
) -> list[dict[str, Any]] | None:
    """Derive must-answer slots from ResearchContract for LoRA/QLoRA/FT queries.

    Returns None when the query is not a LoRA/FT tradeoff (caller uses derive_slots).
    Never marks preference/clinical/abstention slots as critical coverage.
    """
    c = contract if isinstance(contract, ResearchContract) else ResearchContract.from_dict(
        contract if isinstance(contract, dict) else None
    )
    if c is None and brief:
        c = ResearchContract.from_dict(brief.get("research_contract"))
    q = (query or (c.query if c else "") or (brief or {}).get("goal") or "").strip()
    if not is_lora_qlora_ft_query(q) and not (
        c and (c.mandatory_sources or (LORA_QLORA_RE.search(c.query or q) and c.excluded_domains))
    ):
        return None

    # Prefer must_cover labels from contract/brief when they map cleanly; always
    # seed with the canonical FT/LoRA dimension set so retrieval hunts the right slots.
    try:
        from app.domain.textutil import distinctive_terms, user_goal

        goal = user_goal(q) or q
        topic = distinctive_terms(goal, limit=8)
    except Exception:
        goal, topic = q, []

    slots = [_slot_from_spec(spec, topic, goal) for spec in LORA_FT_MUST_ANSWER_SPECS]

    # If must_cover mentions a dimension not in the canonical set, add as non-poison.
    must = list((c.must_cover if c else []) or (brief or {}).get("must_cover") or [])
    seen = {s["id"] for s in slots}
    for item in must:
        label = str(item).strip()
        if not label or is_poison_critical_slot(label=label):
            continue
        sid = re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")[:40]
        if not sid or sid in seen:
            continue
        if is_poison_critical_slot(slot_id=sid, label=label):
            continue
        # Skip if clearly covered by an existing canonical id.
        low = label.lower()
        if any(
            key in low
            for key in (
                "vram",
                "memory",
                "accuracy",
                "cost",
                "ood",
                "forget",
                "quantiz",
                "constraint",
                "limitation",
            )
        ):
            continue
        seen.add(sid)
        slots.append(
            {
                "id": sid,
                "label": label[:120],
                "status": "open",
                "evidence_ids": [],
                "critical": False,
                "priority": "standard",
                "patterns": [re.escape(w) for w in low.split()[:6] if len(w) > 2],
                "topic_terms": topic[:8],
                "followup": f"{goal[:100]} {label}"[:200],
                "aspect": sid,
                "from_contract": True,
            }
        )

    # Safety: strip any poison that slipped in; never leave them critical.
    cleaned: list[dict[str, Any]] = []
    for s in slots:
        if is_poison_critical_slot(slot_id=str(s.get("id") or ""), label=str(s.get("label") or "")):
            continue
        cleaned.append(s)
    if cleaned and not any(s.get("critical") for s in cleaned):
        cleaned[0]["critical"] = True
        cleaned[0]["priority"] = "critical"
    return cleaned or None


def filter_poison_must_answer_slots(
    slots: list[dict[str, Any]] | None,
    query: str = "",
    *,
    demote_only: bool = False,
) -> list[dict[str, Any]]:
    """Drop or demote preference/clinical slots for LoRA/FT queries."""
    rows = [dict(s) for s in (slots or [])]
    if not rows:
        return rows
    if not (is_lora_qlora_ft_query(query) or LORA_QLORA_RE.search(query or "")):
        return rows
    out: list[dict[str, Any]] = []
    for s in rows:
        if is_poison_critical_slot(slot_id=str(s.get("id") or ""), label=str(s.get("label") or "")):
            if demote_only:
                s["critical"] = False
                s["priority"] = "standard"
                out.append(s)
            continue
        out.append(s)
    return out

