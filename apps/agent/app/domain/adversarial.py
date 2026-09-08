"""Adversarial research method: hypotheses, counter-evidence, claim discipline.

Turns a survey-style synthesis into a falsifiable memo: compete two hypotheses,
hunt numbers, mark paper-says vs inference, and refuse SOTA claims from stale sources.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.schema import AgentName, SubQuery
from app.domain.scholar_query import compact_retrieval_query, is_orchestration_topic, topic_terms_from_goal
from app.domain.textutil import user_goal

KIND_ALIASES = {
    "paper_says": "direct",
    "direct": "direct",
    "derived": "derived",
    "inferred": "inferred",
    "inference": "inferred",
    "recommendation": "recommendation",
    "heuristic": "recommendation",
    "speculative": "speculative",
}
KIND_CAP = {
    "direct": 0.88,
    "derived": 0.75,
    "inferred": 0.62,
    "recommendation": 0.55,
    "speculative": 0.42,
}
VERIFY_CAP = {
    "verified": 0.90,
    "inferred": 0.62,
    "pending": 0.70,
    "source_missing": 0.40,
    "unsupported": 0.32,
    "wrong_number": 0.28,
    "wrong_causal": 0.22,
}

QUALITY_BAND = {
    "official_regulation": "S",
    "intergovernmental": "S",
    "standard_body": "S",
    "peer_reviewed": "A",  # arXiv and similar: research-grade, not automatically venue-accepted
    "specialist_research": "B",  # preprint / tech report â€” not venue peer-reviewed
    "industry_association": "B",
    "news_analysis": "C",
    "vendor_or_consultancy": "C",
    "unknown": "C",
}

BAND_LABEL = {
    "S": "S â€” primary docs / official benchmark / accepted venue",
    "A": "A â€” research paper or reference implementation",
    "B": "B â€” survey, institution note",
    "C": "C â€” blog, vendor, secondary commentary",
}

YEAR_RE = re.compile(r"\b(20[12]\d)\b")
# Measured quantities: %, latencies, FLOPs, throughput, counts, speedups.
QUANT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%|"
    r"\b(\d{1,3}(?:,\d{3})+|\d{2,6})\s+(tasks?|files?|models?|agents?|steps?|runs?|papers?|"
    r"artifacts?|studies?|parameters?|tokens?|nodes?|gpus?|epochs?)\b|"
    r"\b(n)\s*=\s*(\d+)\b|"
    r"\b(\d+(?:\.\d+)?)\s*(ms|Âµs|us|s|sec|seconds?|minutes?|min)\b|"
    r"\b(\d+(?:\.\d+)?)\s*((?:G|T|P)?FLOP(?:s|/s)?|TFLOPS?|GFLOPS?)\b|"
    r"\b(\d+(?:\.\d+)?)\s*(tokens?(?:/(?:s|sec|second))?|req(?:uests)?/s|tok/s|GB/s|GiB|GB|TB)\b|"
    r"\b(\d+(?:\.\d+)?)\s*(?:Ã—|x)\s*(?:faster|speedup|improvement|throughput)?\b",
    re.I,
)

# Heading-like line that starts a paper's Results/Findings/Evaluation section.
# Full-text scrapes put abstract+intro first; the numbers we want to table live
# past a naive head-of-document slice, so hunt for this section explicitly.
_RESULTS_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,4}\s*)?(?:\d+[.)]\s*)?"
    r"(?:results?|findings?|experiments?(?:\s+and\s+results?)?|evaluation(?:\s+results?)?|"
    r"empirical\s+(?:results?|study)|quantitative\s+results?)\b\s*[:.]?\s*$"
)


def results_section_blob(full_text: str, *, max_len: int = 6000) -> str:
    """Slice from the first Results/Findings/Evaluation heading onward.

    Returns "" when no such heading is found so callers fall back to a
    head-of-document slice instead of silently scanning the abstract.
    """
    text = full_text or ""
    if not text.strip():
        return ""
    match = _RESULTS_HEADING_RE.search(text)
    if not match:
        return ""
    return text[match.start() : match.start() + max_len]


# Patterns for validating grounded quantitative claims
BENCHMARK_RE = re.compile(
    r"\b(on|in)\s+([A-Z][A-Za-z0-9-]+(?:\s+[A-Z][A-Za-z0-9-]+)?)\b",
    re.I,
)
CONDITION_RE = re.compile(
    r"\b(with|using|when|for|given|under|on|in)\s+([^,\.]{10,80})",
    re.I,
)
BASELINE_RE = re.compile(
    r"\b(vs\.?|versus|compared to|from|baseline)\s+(\d+(?:\.\d+)?%?)",
    re.I,
)
METRIC_NAME_RE = re.compile(
    r"\b(accuracy|precision|recall|F1|BLEU|ROUGE|perplexity|latency|throughput|"
    r"speedup|improvement|gain|loss|error|FLOP(?:s)?|tokens?/s|req/s)\b",
    re.I,
)


def validate_quantitative_claim(text: str, strict: bool = True) -> dict:
    """Validate if a quantitative claim is properly grounded.
    
    Args:
        text: Text containing the quantitative claim
        strict: If True, requires metric + condition + (benchmark OR baseline)
                If False, requires only metric + (condition OR benchmark)
    
    Returns:
        dict with:
        - is_valid: bool
        - has_metric: bool
        - has_benchmark: bool
        - has_condition: bool
        - has_baseline: bool
        - confidence: "empirical" | "reported" | "weak"
        - issues: list of missing elements
    """
    # Check for numeric value
    has_number = bool(QUANT_RE.search(text))
    
    # Check for metric name
    metric_match = METRIC_NAME_RE.search(text)
    has_metric = bool(metric_match)
    
    # Check for benchmark/dataset
    benchmark_match = BENCHMARK_RE.search(text)
    has_benchmark = bool(benchmark_match)
    
    # Check for condition
    condition_match = CONDITION_RE.search(text)
    has_condition = bool(condition_match)
    
    # Check for baseline comparison
    baseline_match = BASELINE_RE.search(text)
    has_baseline = bool(baseline_match)
    
    # Determine validity
    issues = []
    
    if not has_number:
        issues.append("Missing numeric value")
    
    if not has_metric:
        issues.append("Missing metric name (accuracy, latency, etc.)")
    
    if strict:
        # Strict: requires metric + condition + (benchmark OR baseline)
        if not has_condition:
            issues.append("Missing condition (with X, using Y, when Z)")
        if not (has_benchmark or has_baseline):
            issues.append("Missing benchmark/dataset OR baseline comparison")
        
        is_valid = has_number and has_metric and has_condition and (has_benchmark or has_baseline)
    else:
        # Moderate: requires metric + (condition OR benchmark)
        if not (has_condition or has_benchmark):
            issues.append("Missing either condition OR benchmark")
        
        is_valid = has_number and has_metric and (has_condition or has_benchmark)
    
    # Determine confidence level
    if has_baseline:
        confidence = "empirical"  # Has comparison to baseline
    elif has_benchmark and has_condition:
        confidence = "reported"  # Well-specified finding
    else:
        confidence = "weak"  # Incomplete grounding
    
    return {
        "is_valid": is_valid,
        "has_metric": has_metric,
        "has_benchmark": has_benchmark,
        "has_condition": has_condition,
        "has_baseline": has_baseline,
        "confidence": confidence,
        "issues": issues,
    }


# Back-compat alias used by older imports/tests.
PCT_RE = QUANT_RE
ABSOLUTE_RE = re.compile(
    r"\b(do not possess|cannot|never|always|fully autonomous|unconstrained|"
    r"no (?:real )?autonomy|emergent (?:general )?intelligence)\b",
    re.I,
)
ADVERSARIAL_RE = re.compile(
    r"\b(or is|or are|genuinely|actually|really|merely|just orchestration|"
    r"autonom(?:y|ous)|vs\.?|versus|counter|debate|whether)\b",
    re.I,
)


def quality_band(tier: str) -> str:
    return QUALITY_BAND.get((tier or "").strip().lower(), "C")


PREDATORY_HOST_MARKERS = (
    "ijsr.net",
    "ijsr.org",
    "ijsra.net",
    "ijert.org",
    "ijser.org",
    "iaras.org",
    "omicsonline.org",
    "scirp.org",
)
PREDATORY_TITLE_MARKERS = (
    "international journal of science and research",
    "international journal of scientific research",
    "international journal of engineering research",
)


def is_predatory_venue(url: str = "", title: str = "") -> bool:
    u = (url or "").lower()
    title_l = (title or "").lower()
    if any(m in u for m in PREDATORY_HOST_MARKERS):
        return True
    if "doi.org/10.21275" in u:
        return True
    return any(m in title_l for m in PREDATORY_TITLE_MARKERS)


def quality_band_for(url: str = "", tier: str = "", title: str = "") -> str:
    """URL-aware band: preprints and predatory venues are never Band A peer-reviewed."""
    u = (url or "").lower()
    path = u.split("github.com")[-1] if "github.com" in u else u
    if is_predatory_venue(u, title):
        return "C"
    if "awesome" in path or "/awesome-" in path or path.rstrip("/").endswith("-list"):
        return "C"
    if "github.com" in u or "gitlab.com" in u:
        return "B"
    if "arxiv.org" in u or "export.arxiv.org" in u:
        return "B"
    if "openreview.net" in u and "/forum" in u:
        return "B"
    return quality_band(tier)

ASSUMPTION_RE = re.compile(
    r"\b("
    r"estimat(?:e|ed|es|ing|ion)|assum(?:e|ed|es|ption)|reported (?:compute|figure|estimate)|"
    r"illustrative|hypothetical|we (?:use|take|set|adopt).{0,60}\bas\b|"
    r"approximate(?:ly)?|putative|purported"
    r")\b",
    re.I,
)
MEASURED_RE = re.compile(
    r"\b(we measure|measured|measurement|empirical(?:ly)?|ablation|table\s+\d+|n\s*=\s*\d+|pass@\d+)\b",
    re.I,
)
SECONDHAND_RE = re.compile(r"\b(according to|cited (?:in|by)|as reported by|second[- ]hand)\b", re.I)


def infer_provenance(claim_text: str, source_text: str = "") -> str:
    """measured | author_assumption | secondhand | unknown â€” orthogonal to quote-match verify."""
    claim = claim_text or ""
    source = source_text or ""
    blob = f"{claim} {source}"
    if ASSUMPTION_RE.search(blob):
        return "author_assumption"
    if SECONDHAND_RE.search(blob):
        return "secondhand"
    if MEASURED_RE.search(source) or MEASURED_RE.search(claim):
        return "measured"
    return "unknown"


def normalize_kind(kind: str, *, has_quote: bool = False) -> str:
    raw = (kind or "").strip().lower()
    if raw in KIND_ALIASES:
        return KIND_ALIASES[raw]
    return "direct" if has_quote else "inferred"


def publication_status(ev: dict | str) -> str:
    if isinstance(ev, str):
        url, tier, title = ev.lower(), "", ""
    else:
        url = str((ev or {}).get("url") or "").lower()
        tier = str((ev or {}).get("tier") or "").lower()
        title = str((ev or {}).get("title") or "")
    if is_predatory_venue(url, title):
        return "predatory_or_unreliable"
    if "arxiv.org" in url:
        return "preprint"
    if "github.com" in url and "awesome" in url:
        return "aggregator"
    if tier in {"official_regulation", "standard_body", "intergovernmental"}:
        return "standard"
    if tier == "peer_reviewed":
        return "peer_reviewed"
    if tier == "specialist_research":
        return "technical_report"
    if tier in {"news_analysis", "vendor_or_consultancy"}:
        return "secondary"
    return "unknown"


def _field(claim: Any, key: str, default: Any = None) -> Any:
    if isinstance(claim, dict):
        return claim.get(key, default)
    return getattr(claim, key, default)


def independent_source_count(claim: Any, evidence: list[dict] | None = None) -> int:
    from app.domain.coverage import work_identity

    works: set[str] = set()
    url = str(_field(claim, "url") or "")
    title = str(_field(claim, "title") or "")
    if url or title:
        works.add(work_identity(url, title))
    support = _field(claim, "support_ids") or []
    by_id = {str(e.get("id") or ""): e for e in (evidence or [])}
    for sid in support:
        ev = by_id.get(str(sid)) or {}
        works.add(work_identity(str(ev.get("url") or ""), str(ev.get("title") or "")))
    # Also parse support citation marks like [3], [7] if present as urls list
    for extra in _field(claim, "support_urls") or []:
        works.add(work_identity(str(extra), ""))
    works.discard("")
    works.discard("title:")
    return len(works)


def recalibrate_claim_confidence(claim: Any, evidence: list[dict] | None = None) -> float:
    """Cap High so inference / single-lab / contradicted claims cannot look settled."""
    kind = normalize_kind(str(_field(claim, "kind") or ""), has_quote=bool(_field(claim, "quote")))
    status = str(_field(claim, "verification_status") or "pending")
    raw = float(_field(claim, "confidence") or 0.55)
    cap = min(KIND_CAP.get(kind, 0.62), VERIFY_CAP.get(status, 0.70))
    if _field(claim, "contradict_ids"):
        cap = min(cap, 0.58)
        raw -= 0.12
    indep = independent_source_count(claim, evidence)
    if indep < 2:
        cap = min(cap, 0.62)
    provenance = str(_field(claim, "provenance") or "")
    if provenance == "author_assumption":
        cap = min(cap, 0.52)
    elif provenance == "secondhand":
        cap = min(cap, 0.48)
    text = str(_field(claim, "text") or "")
    if indep < 2 and re.search(r"\d", text):
        # Single-sourced quantitative claims must not look like consensus.
        cap = min(cap, 0.55)
    return round(min(cap, max(0.22, raw)), 3)


def year_of(ev: dict) -> int | None:
    for field in (ev.get("published"), ev.get("title"), ev.get("url"), ev.get("snippet"), ev.get("quote")):
        match = YEAR_RE.search(str(field or ""))
        if match:
            year = int(match.group(1))
            if 2012 <= year <= 2028:
                return year
    return None


def competing_hypotheses(query: str) -> list[str]:
    goal = user_goal(query) or (query or "").strip()
    if ADVERSARIAL_RE.search(goal):
        return [
            f"H1 â€” Orchestration: the pattern in â€œ{goal[:140]}â€ is mostly engineered control, evaluation setup, or harness design around a probabilistic model.",
            f"H2 â€” Capability: frontier models already contribute general planning/adaptation, and the harness mainly amplifies that capability.",
        ]
    return [
        f"H1 â€” The conservative reading of â€œ{goal[:140]}â€ is explained by system design, measurement setup, or surrounding infrastructure.",
        f"H2 â€” The same question is explained primarily by model capability, with infrastructure as a secondary amplifier.",
    ]


def research_subquestions(query: str, hypotheses: list[str] | None = None) -> list[str]:
    goal = user_goal(query) or (query or "").strip()
    hyps = hypotheses or competing_hypotheses(query)
    h1 = hyps[0] if hyps else "H1"
    h2 = hyps[1] if len(hyps) > 1 else "H2"
    return [
        f"What operational definition would make â€œ{goal[:120]}â€ testable?",
        f"What primary-source evidence would support {h1[:160]}",
        f"What primary-source evidence would support {h2[:160]}",
        "What quantitative results exist (benchmark, N, success rate, delta, cost, steps)?",
        "What findings contradict the most convenient answer?",
        "Which sources are too old to support a current state-of-the-art claim?",
        "Which statements are paper findings vs agent inference?",
    ]


def falsification_queries(query: str, brief: dict | None = None) -> list[SubQuery]:
    goal = user_goal(query) or (query or "").strip()
    hyps = (brief or {}).get("hypotheses") or competing_hypotheses(query)
    h1 = hyps[0] if hyps else "the default thesis"
    core = " ".join(topic_terms_from_goal(goal, 5)) or goal[:80]
    subs = [
        SubQuery(
            agent=AgentName.SCHOLAR,
            question=compact_retrieval_query(
                f"{core} counter-evidence falsification",
                goal=goal,
                agent="scholar",
            ),
            rationale="Collect evidence that could falsify the convenient thesis.",
        ),
        SubQuery(
            agent=AgentName.SEARCH,
            question=compact_retrieval_query(
                f"evidence against {h1[:100]}",
                goal=goal,
                agent="search",
            ),
            rationale="Explicit counter-hypothesis search.",
        ),
        SubQuery(
            agent=AgentName.SCHOLAR,
            question=compact_retrieval_query(
                f"{core} benchmark evaluation metrics",
                goal=goal,
                agent="scholar",
            ),
            rationale="Prefer measured numbers over qualitative survey language.",
        ),
    ]
    if is_orchestration_topic(goal):
        subs.append(
            SubQuery(
                agent=AgentName.SCHOLAR,
                question=compact_retrieval_query(
                    f"{core} failure modes ablation study",
                    goal=goal,
                    agent="scholar",
                ),
                rationale="Strongest contradicting regime for orchestration/control claims.",
            )
        )
    else:
        subs.append(
            SubQuery(
                agent=AgentName.SCHOLAR,
                question=compact_retrieval_query(
                    f"{core} limitations distribution shift",
                    goal=goal,
                    agent="scholar",
                ),
                rationale="Find boundary conditions and generalization limits.",
            )
        )
    return subs


BENCHMARK_RE = re.compile(
    r"\b("
    r"SWE-bench(?:\s+Verified)?|HumanEval|MBPP|GAIA|WebArena|BrowserGym|"
    r"AgentBench|ToolBench|API-Bank|BFCL|Ï„-bench|tau-bench|"
    r"LiveCodeBench|BigCodeBench|SciCode|GPQA|MMLU(?:-Pro)?|"
    r"AIME|MATH(?:-500)?|GSM8K|HotpotQA|TriviaQA|"
    r"ORAgentBench|MemGym|PAST-Bench|RAMP|LiveClawBench"
    r")\b",
    re.I,
)


def numeric_evidence_score(ev: dict) -> float:
    """Boost sources whose excerpts contain measured benchmarks or outcome numbers."""
    blob = " ".join(
        str(ev.get(key) or "")
        for key in ("title", "snippet", "quote")
    ) + " " + str(ev.get("full_text") or "")[:3500]
    if not blob.strip():
        return 0.0
    score = 0.0
    matches = list(QUANT_RE.finditer(blob))
    score += min(3.0, len(matches) * 0.55)
    if BENCHMARK_RE.search(blob):
        score += 2.0
    if MEASURED_RE.search(blob):
        score += 1.0
    if re.search(r"\b(survey|systematic review|literature review|overview)\b", blob, re.I) and not matches:
        score -= 1.25
    return max(0.0, score)


def retrieval_rank_score(ev: dict, query: str = "") -> float:
    from app.domain.research_intent import authority_score, topic_relevance_penalty

    weight = numeric_rank_weight(query)
    blob = " ".join(str(ev.get(k) or "") for k in ("title", "snippet", "quote"))
    # A named-benchmark result fragment (e.g. "62.4% on SWE-bench") is on-topic
    # for any ML-systems question even when it doesn't echo the query's exact
    # wording — only penalize sources with no such signal AND no shared term.
    penalty = 0.0 if BENCHMARK_RE.search(blob) else topic_relevance_penalty(ev, query)
    return authority_score(ev) + numeric_evidence_score(ev) * weight + penalty


def numeric_rank_weight(query: str = "") -> float:
    """Lower numeric boost for architecture/why questions; higher for benchmark-heavy asks."""
    if not (query or "").strip():
        return 0.7
    low = query.lower()
    if re.search(
        r"benchmark|accuracy|latency|throughput|%\s|\bms\b|tok/s|swe-bench|evaluat|numbers?",
        low,
    ):
        return 1.0
    from app.domain.decompose import derive_slots

    slot_ids = {str(s.get("id") or "") for s in derive_slots(query, use_llm=False)}
    analytical = {"mechanism", "constraints", "scalability", "direct_answer", "comparison"}
    if "quantitative" in slot_ids and not slot_ids.intersection(analytical - {"comparison"}):
        return 1.0
    if slot_ids.intersection(analytical):
        return 0.35
    return 0.7


def extract_quantitative_rows(evidence: list[dict], citations: list[dict] | None = None) -> list[dict[str, Any]]:
    url_to_n = {
        (c.get("url") or "").rstrip("/").lower(): c.get("n")
        for c in (citations or [])
        if c.get("url")
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ev in evidence or []:
        full_text = ev.get("full_text") or ""
        focused = results_section_blob(full_text)
        blob = (
            f"{ev.get('title', '')} {ev.get('snippet', '')} {ev.get('quote', '')} "
            f"{focused or full_text[:4500]}"
        )
        url = (ev.get("url") or "").rstrip("/").lower()
        n = url_to_n.get(url, "?")
        for match in QUANT_RE.finditer(blob):
            token = re.sub(r"\s+", " ", match.group(0)).strip()
            key = f"{n}:{token.lower()}"
            if key in seen or len(token) < 2:
                continue
            start = max(0, match.start() - 140)
            end = min(len(blob), match.end() + 140)
            window = blob[start:end]
            if _is_setup_parameter(token, window):
                continue
            
            # SEMANTIC GATE: Require metric/unit AND experimental condition
            if not _has_valid_metric_and_condition(token, window):
                continue
            
            seen.add(key)
            condition = _metric_condition(window)
            benchmark = _benchmark_name(window, blob[:500])
            verified_bench = benchmark != "unverified benchmark"
            
            # Additional filter: drop if condition is still generic/missing after validation
            if condition == "condition not stated in excerpt":
                continue
            
            rows.append(
                {
                    "n": n,
                    "metric": token,
                    "metric_name": token,
                    "value": token,
                    "benchmark_name": benchmark,
                    "condition": condition,
                    "comparison_baseline": "not explicitly compared",
                    "evaluation_setup": condition,
                    "warning": "" if verified_bench else "Unverified Benchmark",
                    "title": (ev.get("title") or "")[:80],
                    "year": year_of(ev),
                    "band": quality_band_for(str(ev.get("url") or ""), str(ev.get("tier") or ""), str(ev.get("title") or "")),
                    "url": ev.get("url") or "",
                }
            )
            if len(rows) >= 24:
                return rows
    return rows


def _is_setup_parameter(token: str, window: str = "") -> bool:
    """True for experiment-config counts that should not pad the outcome metrics table."""
    t = (token or "").lower()
    w = (window or "").lower()
    # Keep clear outcome units.
    if re.search(
        r"%|\bms\b|Âµs|\bus\b|\btflop|\bgflop|\bflop|tok(?:ens)?/s|gb/s|gib|"
        r"Ã—|x\s*(?:faster|speedup|improvement)",
        t,
    ):
        return False
    # Corpus / protocol sizes.
    if re.search(r"\b\d[\d,]*\s+(studies?|papers?|artifacts?|runs?|epochs?)\b", t):
        return True
    # Token lengths used as ISL/OSL / sizing config.
    if re.search(r"\b\d[\d,]*\s+tokens?\b", t):
        if re.search(
            r"\b(isl|osl|input\s+sequence|output\s+sequence|sequence\s+length|"
            r"prompt\s+length|sizing|configuration|benchmark\s+config|"
            r"context\s+window)\b",
            w,
        ):
            return True
        # Bare token counts without latency/cost/throughput context â†’ setup.
        if not re.search(
            r"\b(latency|ttft|throughput|cost|pre-?fill|decode|generated|error|accurac)\b",
            w,
        ):
            return True
    return False


def _benchmark_name(window: str, title_blob: str = "") -> str:
    for blob in (window, title_blob):
        match = BENCHMARK_RE.search(blob or "")
        if match:
            return match.group(1)
    return "unverified benchmark"


def _metric_condition(window: str) -> str:
    low = (window or "").lower()
    if re.search(r"\bhard(?:[- ]tasks?)?\b", low):
        return "hard-task subset"
    if re.search(r"\beasy(?:[- ]tasks?)?\b", low):
        return "easy-task subset"
    if re.search(r"\ball\s+tasks\b|\boverall\b", low):
        return "all tasks / overall"
    if re.search(r"\bbaseline\b", low):
        return "baseline condition"
    if re.search(r"\bkv[- ]?cache|multi[- ]?node|cluster|gpu\s*memory|hbm\b", low):
        return "scalability / systems regime"
    return "condition not stated in excerpt"


def _has_valid_metric_and_condition(token: str, window: str) -> bool:
    """Semantic gate: require metric/unit name AND experimental condition.
    
    Drops bare numbers like "1970s", "16%", "53%" without context.
    Returns True only if the number has BOTH:
    1. A metric/unit name (accuracy, latency, FLOP, etc.)
    2. An experimental condition (dataset, setup, benchmark, etc.)
    """
    t = (token or "").lower()
    w = (window or "").lower()
    
    # Check for valid metric/unit in token or nearby context
    # Clear outcome metrics with units
    has_metric = bool(re.search(
        r"%|accuracy|error|precision|recall|f1|"
        r"\bms\b|µs|\bus\b|seconds?|minutes?|latency|ttft|throughput|"
        r"tflop|gflop|\bflop|tok(?:ens)?/s|req(?:uests)?/s|"
        r"gb/s|gib|gb|tb|memory|bandwidth|"
        r"×|x\s*(?:faster|speedup|improvement)|"
        r"cost|price|\$|tokens?|parameters?",
        t + " " + w,
    ))
    
    # Bare years without metric context are not valid
    if re.match(r"^\d{4}s?$", t.strip()):
        return False
    
    # Bare percentages without outcome metric context
    if re.search(r"^\d+(?:\.\d+)?%$", t.strip()) and not re.search(
        r"accuracy|error|precision|recall|improvement|reduction|increase|decrease|"
        r"pass@\d+|success|failure|correct|incorrect",
        w,
    ):
        return False
    
    # Check for experimental condition in context
    # Require specific named benchmarks, datasets, or experimental setups
    has_condition = bool(re.search(
        r"\b("
        r"dataset|benchmark|task\s+(?:subset|set)|test\s+set|evaluation\s+(?:set|setup)|"
        r"on\s+(?:the\s+)?(?:\w+\s+)?(?:dataset|benchmark|task)|"
        r"swe-bench|humaneval|mbpp|gaia|webarena|browsergym|agentbench|"
        r"mmlu|gpqa|math|gsm8k|hotpotqa|triviaqa|livecodebench|"
        r"baseline|ablation(?:\s+study)?|condition|setting|scenario|"
        r"hard\s+task|easy\s+task|all\s+tasks|subset|"
        r"gpu|node|cluster|kv[- ]cache|batch\s+size|model\s+size|"
        r"vs\.?|versus|compared\s+to|against\s+"
        r")\b",
        w,
        re.I,
    ))
    
    # "experiment" alone without specific benchmark/dataset is too generic
    if not has_condition and re.search(r"\bexperiment\b", w, re.I):
        # Check if there's a specific experimental setup mentioned
        if re.search(r"(?:in\s+(?:the|our|this)\s+)?experiment(?:al)?\s+(?:setup|configuration|protocol)", w, re.I):
            has_condition = True
    
    return has_metric and has_condition


def temporal_warnings(evidence: list[dict], horizon: str = "") -> list[str]:
    horizon_years = [int(y) for y in YEAR_RE.findall(horizon or "")]
    target = max(horizon_years) if horizon_years else 2026
    warnings: list[str] = []
    for ev in evidence or []:
        year = year_of(ev)
        if year is None:
            continue
        if target - year >= 3:
            title = (ev.get("title") or ev.get("url") or "source")[:90]
            warnings.append(
                f"{title} ({year}) is too old to be the sole support for a {target} state-of-the-art claim."
            )
        if len(warnings) >= 8:
            break
    return warnings


def source_quality_rows(evidence: list[dict], citations: list[dict] | None = None) -> list[dict[str, Any]]:
    url_to_n = {
        (c.get("url") or "").rstrip("/").lower(): c.get("n")
        for c in (citations or [])
        if c.get("url")
    }
    rows = []
    for ev in evidence or []:
        url = (ev.get("url") or "").rstrip("/").lower()
        rows.append(
            {
                "n": url_to_n.get(url, "?"),
                "title": (ev.get("title") or url or "untitled")[:90],
                "tier": ev.get("tier") or "unknown",
                "band": quality_band_for(str(ev.get("url") or ""), str(ev.get("tier") or ""), str(ev.get("title") or "")),
                "publication_status": publication_status(ev),
                "year": year_of(ev),
            }
        )
    return rows


def overclaim_reasons(text: str) -> list[str]:
    from app.domain.report_audit import audit_memo

    notes = []
    if ABSOLUTE_RE.search(text or ""):
        notes.append(
            "Absolute autonomy / unconstrained-capability wording is stronger than typical benchmark evidence; qualify to bounded autonomy unless a primary source uses those words."
        )
    notes.extend(audit_memo(text or ""))
    return notes


def method_notes_for_writer(
    query: str,
    brief: dict | None,
    evidence: list[dict],
    citations: list[dict],
) -> str:
    brief = brief or {}
    hyps = brief.get("hypotheses") or competing_hypotheses(query)
    subs = brief.get("subquestions") or research_subquestions(query, hyps)
    numbers = extract_quantitative_rows(evidence, citations)
    stale = temporal_warnings(evidence, str(brief.get("time_horizon") or ""))
    quality = source_quality_rows(evidence, citations)
    lines = [
        "Adversarial method (follow this; do not bury it):",
        "Hypotheses to keep in tension â€” argue ONCE under Contradictions & debates:",
        *[f"- {h}" for h in hyps[:2]],
        "Subquestions:",
        *[f"- {s}" for s in subs[:8]],
        "Attribution discipline: never write that this memo/pipeline surveyed N papers. "
        "If a source reviewed N artifacts, attribute that count to [n].",
        "Scalability: if the question asks for it, treat KV-cache / GPU memory bandwidth / "
        "multi-node as its own analysis subsection â€” not a latency synonym.",
        "Worked example: when â‰¥2 named systems appear in notes, include one concrete walkthrough.",
        "Quantitative fragments already in the working set (table these; do not pad empties):",
    ]
    if numbers:
        lines.extend(
            f"- [{row['n']}] {row['metric']} â€” condition: {row.get('condition') or 'unset'}; "
            f"baseline: {row.get('comparison_baseline') or 'not explicitly compared'} â€” "
            f"{row['title']} ({row['year'] or 'year?'}, {row['band']})"
            for row in numbers
        )
    else:
        lines.append("- None extracted. Do not invent percentages. Say measurements were not found.")
    lines.append("Source quality:")
    lines.extend(
        f"- [{row['n']}] {row['band']}/{row.get('publication_status') or 'unknown'} {row['title']} ({row['year'] or 'year?'})"
        for row in quality[:16]
    )
    if stale:
        lines.append("Temporal cautions:")
        lines.extend(f"- {w}" for w in stale)
    return "\n".join(lines)


def claim_register_markdown(claims: list[Any], citations: list[dict] | None = None) -> str:
    if not claims:
        return "_No structured claims extracted._"
    evidence = []
    for c in citations or []:
        evidence.append({"id": c.get("evidence_id") or c.get("id"), "url": c.get("url"), "title": c.get("title")})
    header = (
        "| Claim | Kind | Provenance | Verify | Indep. sources | Flags | Support | Year | Band | Conf |\n"
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [header]
    verify_label = {
        "verified": "quote-matched",
        "wrong_number": "wrong-number",
        "wrong_causal": "wrong-causal",
        "unsupported": "unsupported",
        "source_missing": "source-missing",
        "inferred": "inference",
        "pending": "pending",
    }
    for claim in claims[:12]:
        text = _field(claim, "text") or ""
        kind = normalize_kind(str(_field(claim, "kind") or ""), has_quote=bool(_field(claim, "quote")))
        prov = _field(claim, "provenance") or "unknown"
        verify = verify_label.get(str(_field(claim, "verification_status") or ""), _field(claim, "verification_status") or "â€”")
        indep = independent_source_count(claim, evidence)
        flags = []
        if indep < 2:
            flags.append("single-sourced")
        if prov == "author_assumption":
            flags.append("author-estimate")
        if prov == "secondhand":
            flags.append("secondhand")
        support = ", ".join(str(x) for x in (_field(claim, "support_ids") or [])[:3]) or "â€”"
        year = _field(claim, "published") or "â€”"
        band = _field(claim, "quality_band") or quality_band_for(
            str(_field(claim, "url") or ""), str(_field(claim, "tier") or "")
        )
        conf = _field(claim, "confidence")
        pct = f"{int(float(conf) * 100)}%" if conf is not None else "â€”"
        rows.append(
            f"| {(text or '')[:70]} | {kind} | {prov} | {verify} | {indep} | "
            f"{', '.join(flags) or 'â€”'} | {support} | {year} | {band} | {pct} |"
        )
    return "\n".join(rows)


def quantitative_table_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No numeric results were extracted from the collected sources. Do not invent them."
    lines = [
        "| Source | Metric | Value | Benchmark | Condition | Baseline | Flag | Year |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| [{row.get('n')}] | {row.get('metric_name') or row.get('metric')} | "
            f"{row.get('value') or row.get('metric')} | {row.get('benchmark_name') or 'unverified benchmark'} | "
            f"{row.get('condition') or 'â€”'} | {row.get('comparison_baseline') or 'not explicitly compared'} | "
            f"{row.get('warning') or 'â€”'} | {row.get('year') or 'â€”'} |"
        )
    return "\n".join(lines)
