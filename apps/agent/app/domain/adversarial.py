"""Adversarial research method: hypotheses, counter-evidence, claim discipline.

Turns a survey-style synthesis into a falsifiable memo: compete two hypotheses,
hunt numbers, mark paper-says vs inference, and refuse SOTA claims from stale sources.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.schema import AgentName, SubQuery
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
    "specialist_research": "A",
    "industry_association": "B",
    "news_analysis": "C",
    "vendor_or_consultancy": "C",
    "unknown": "C",
}

BAND_LABEL = {
    "S": "S — primary docs / official benchmark / accepted venue",
    "A": "A — research paper or reference implementation",
    "B": "B — survey, institution note",
    "C": "C — blog, vendor, secondary commentary",
}

YEAR_RE = re.compile(r"\b(20[12]\d)\b")
# Measured quantities: %, latencies, FLOPs, throughput, counts, speedups.
QUANT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%|"
    r"\b(\d{1,3}(?:,\d{3})+|\d{2,6})\s+(tasks?|files?|models?|agents?|steps?|runs?|papers?|"
    r"artifacts?|studies?|parameters?|tokens?|nodes?|gpus?|epochs?)\b|"
    r"\b(n)\s*=\s*(\d+)\b|"
    r"\b(\d+(?:\.\d+)?)\s*(ms|µs|us|s|sec|seconds?|minutes?|min)\b|"
    r"\b(\d+(?:\.\d+)?)\s*((?:G|T|P)?FLOP(?:s|/s)?|TFLOPS?|GFLOPS?)\b|"
    r"\b(\d+(?:\.\d+)?)\s*(tokens?(?:/(?:s|sec|second))?|req(?:uests)?/s|tok/s|GB/s|GiB|GB|TB)\b|"
    r"\b(\d+(?:\.\d+)?)\s*(?:×|x)\s*(?:faster|speedup|improvement|throughput)?\b",
    re.I,
)
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


def quality_band_for(url: str = "", tier: str = "") -> str:
    """URL-aware band: awesome-lists and aggregators are never Band A."""
    u = (url or "").lower()
    path = u.split("github.com")[-1] if "github.com" in u else u
    if "awesome" in path or "/awesome-" in path or path.rstrip("/").endswith("-list"):
        return "C"
    if "github.com" in u or "gitlab.com" in u:
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
    """measured | author_assumption | secondhand | unknown — orthogonal to quote-match verify."""
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
        url, tier = ev.lower(), ""
    else:
        url = str((ev or {}).get("url") or "").lower()
        tier = str((ev or {}).get("tier") or "").lower()
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
            f"H1 — Orchestration: the pattern in “{goal[:140]}” is mostly engineered control, evaluation setup, or harness design around a probabilistic model.",
            f"H2 — Capability: frontier models already contribute general planning/adaptation, and the harness mainly amplifies that capability.",
        ]
    return [
        f"H1 — The conservative reading of “{goal[:140]}” is explained by system design, measurement setup, or surrounding infrastructure.",
        f"H2 — The same question is explained primarily by model capability, with infrastructure as a secondary amplifier.",
    ]


def research_subquestions(query: str, hypotheses: list[str] | None = None) -> list[str]:
    goal = user_goal(query) or (query or "").strip()
    hyps = hypotheses or competing_hypotheses(query)
    h1 = hyps[0] if hyps else "H1"
    h2 = hyps[1] if len(hyps) > 1 else "H2"
    return [
        f"What operational definition would make “{goal[:120]}” testable?",
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
    return [
        SubQuery(
            agent=AgentName.SCHOLAR,
            question=f"{goal} contrary findings OR counterexample OR fails to",
            rationale="Collect evidence that could falsify the convenient thesis.",
        ),
        SubQuery(
            agent=AgentName.SEARCH,
            question=f"evidence against: {h1[:140]}",
            rationale="Explicit counter-hypothesis search.",
        ),
        SubQuery(
            agent=AgentName.SCHOLAR,
            question=f"{goal} benchmark results success rate comparison",
            rationale="Prefer measured numbers over qualitative survey language.",
        ),
        SubQuery(
            agent=AgentName.SCHOLAR,
            question=f"{goal} failure modes OR ablation OR when FSM OR rollback fails",
            rationale="Strongest contradicting regime for orchestration/control claims.",
        ),
    ]


BENCHMARK_RE = re.compile(
    r"\b("
    r"SWE-bench(?:\s+Verified)?|HumanEval|MBPP|GAIA|WebArena|BrowserGym|"
    r"AgentBench|ToolBench|API-Bank|BFCL|τ-bench|tau-bench|"
    r"LiveCodeBench|BigCodeBench|SciCode|GPQA|MMLU(?:-Pro)?|"
    r"AIME|MATH(?:-500)?|GSM8K|HotpotQA|TriviaQA|"
    r"ORAgentBench|MemGym|PAST-Bench|RAMP|LiveClawBench"
    r")\b",
    re.I,
)


def extract_quantitative_rows(evidence: list[dict], citations: list[dict] | None = None) -> list[dict[str, Any]]:
    url_to_n = {
        (c.get("url") or "").rstrip("/").lower(): c.get("n")
        for c in (citations or [])
        if c.get("url")
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ev in evidence or []:
        blob = (
            f"{ev.get('title', '')} {ev.get('snippet', '')} {ev.get('quote', '')} "
            f"{(ev.get('full_text') or '')[:4500]}"
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
            seen.add(key)
            condition = _metric_condition(window)
            benchmark = _benchmark_name(window, blob[:500])
            verified_bench = benchmark != "unverified benchmark"
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
                    "band": quality_band_for(str(ev.get("url") or ""), str(ev.get("tier") or "")),
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
        r"%|\bms\b|µs|\bus\b|\btflop|\bgflop|\bflop|tok(?:ens)?/s|gb/s|gib|"
        r"×|x\s*(?:faster|speedup|improvement)",
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
        # Bare token counts without latency/cost/throughput context → setup.
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
                "band": quality_band_for(str(ev.get("url") or ""), str(ev.get("tier") or "")),
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
        "Hypotheses to keep in tension — argue ONCE under Contradictions & debates:",
        *[f"- {h}" for h in hyps[:2]],
        "Subquestions:",
        *[f"- {s}" for s in subs[:8]],
        "Attribution discipline: never write that this memo/pipeline surveyed N papers. "
        "If a source reviewed N artifacts, attribute that count to [n].",
        "Scalability: if the question asks for it, treat KV-cache / GPU memory bandwidth / "
        "multi-node as its own analysis subsection — not a latency synonym.",
        "Worked example: when ≥2 named systems appear in notes, include one concrete walkthrough.",
        "Quantitative fragments already in the working set (table these; do not pad empties):",
    ]
    if numbers:
        lines.extend(
            f"- [{row['n']}] {row['metric']} — condition: {row.get('condition') or 'unset'}; "
            f"baseline: {row.get('comparison_baseline') or 'not explicitly compared'} — "
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
        verify = verify_label.get(str(_field(claim, "verification_status") or ""), _field(claim, "verification_status") or "—")
        indep = independent_source_count(claim, evidence)
        flags = []
        if indep < 2:
            flags.append("single-sourced")
        if prov == "author_assumption":
            flags.append("author-estimate")
        if prov == "secondhand":
            flags.append("secondhand")
        support = ", ".join(str(x) for x in (_field(claim, "support_ids") or [])[:3]) or "—"
        year = _field(claim, "published") or "—"
        band = _field(claim, "quality_band") or quality_band_for(
            str(_field(claim, "url") or ""), str(_field(claim, "tier") or "")
        )
        conf = _field(claim, "confidence")
        pct = f"{int(float(conf) * 100)}%" if conf is not None else "—"
        rows.append(
            f"| {(text or '')[:70]} | {kind} | {prov} | {verify} | {indep} | "
            f"{', '.join(flags) or '—'} | {support} | {year} | {band} | {pct} |"
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
            f"{row.get('condition') or '—'} | {row.get('comparison_baseline') or 'not explicitly compared'} | "
            f"{row.get('warning') or '—'} | {row.get('year') or '—'} |"
        )
    return "\n".join(lines)
