"""Deterministic post-write critic. No extra LLM call."""

from __future__ import annotations

import re

from app.domain.metric_grounding import audit_memo_causal_deltas

EXPONENTIAL_TOKEN_RE = re.compile(
    r"exponentially larger (?:set|number) of (?:key-value pairs|tokens|premises)|"
    r"error probability compounds exponentially",
    re.I,
)
ORIGINATE_RE = re.compile(
    r"all primary assertions.{0,80}originate directly from these sources",
    re.I,
)
BOOST_RE = re.compile(r"\+\s*\d+(?:\.\d+)?%\s+boost", re.I)
THRESHOLD_RE = re.compile(r"(?:≤|>=|<=|<)\s*\d+\s*k\b|(?:≤|>=|<=)\s*\d+\s*hops?", re.I)
LITM_RE = re.compile(r"lost[- ]in[- ]the[- ]middle", re.I)
SCOPE_RE = re.compile(
    r"\b(20\d{2}|benchmark|eval|ruler|longbench|nolima|needle|gpt|claude|gemini|llama|qwen)\b",
    re.I,
)
# Claims Kiln surveyed N papers when N comes from a cited source's corpus.
PIPELINE_CORPUS_RE = re.compile(
    r"(?:empirical\s+)?(?:synthesis|review|analysis|survey)\s+across\s+[\d,]+\s+"
    r"(?:research\s+)?(?:artifacts|papers|studies|sources)",
    re.I,
)
OUR_CORPUS_RE = re.compile(
    r"\b(?:our|this|kiln(?:'s)?)\s+(?:synthesis|review|pipeline|system|memo)\b.{0,60}"
    r"(?:across|of|from)\s+[\d,]+\s+(?:research\s+)?(?:artifacts|papers|studies)",
    re.I,
)
COUNTER_HEADING_RE = re.compile(r"^####\s*Counter-evidence\s*$", re.I | re.M)
NOT_REPORTED_RE = re.compile(r"\bnot\s+reported\b", re.I)
EPISTEMIC_TAG_RE = re.compile(
    r"\[(?:DIRECT|INFERRED|DERIVED|RECOMMENDATION|SPECULATIVE)\]|"
    r"\b(?:DIRECT|INFERRED|DERIVED|RECOMMENDATION|SPECULATIVE)\s*:\s+",
    re.I,
)
GATED_ROUTING_RE = re.compile(
    r"uncertainty[- ]gated|entropy[- ]gated|logit\s+entropy|confidence[- ]gated\s+routing|"
    r"gated\s+(?:pipeline|routing|fallback)",
    re.I,
)
SETUP_TABLE_ROW_RE = re.compile(
    r"\|\s*\d[\d,]*\s+(?:studies?|papers?|artifacts?|runs?|epochs?|tokens?)\s*\|",
    re.I,
)


def audit_memo(text: str, *, query: str = "") -> list[str]:
    blob = text or ""
    notes: list[str] = []
    if EXPONENTIAL_TOKEN_RE.search(blob):
        notes.append(
            "Mechanistic overclaim: sequence length grows linearly, not exponentially. "
            "Softmax need not dilute uniformly if a few logits dominate. Treat attention-dilution "
            "as a hypothesis, not a proven causal law."
        )
    if ORIGINATE_RE.search(blob):
        notes.append(
            "Do not claim every assertion is source-direct. Synthesis, thresholds, and "
            "recommendations are inferred unless a citation states them."
        )
    if BOOST_RE.search(blob) and "percentage point" not in blob.lower():
        notes.append(
            "Normalize deltas: write absolute change as percentage points and relative change as %. "
            "Do not write “+20% boost” when 42% → 62%."
        )
    if _unscoped_litm(blob):
        notes.append(
            "Scope “lost in the middle”: name the benchmark, models, task, and context regime. "
            "It is not a universal 2026 law for all frontier models."
        )
    if THRESHOLD_RE.search(blob) and "heuristic" not in blob.lower():
        notes.append(
            "Numeric operating envelope (k-token / hop / distractor-ratio cutoffs) is a heuristic "
            "unless a cited experiment establishes that threshold. Label it Heuristic / Confidence low."
        )
    if _misattributed_corpus(blob):
        notes.append(
            "Attribution: a cited paper’s corpus size (e.g. 1,547 artifacts) is that source’s "
            "synthesis — write “Based on [n]’s review of …” not “Empirical synthesis across N” "
            "as if this memo surveyed that set."
        )
    tag_hits = len(EPISTEMIC_TAG_RE.findall(blob))
    if tag_hits >= 4:
        notes.append(
            "Formatting clutter: strip [DIRECT]/[INFERRED]/[DERIVED]/[RECOMMENDATION] tags from "
            "prose — write professional narrative; keep epistemic kind only in the claim register."
        )
    if _quantitative_emptiness(blob):
        notes.append(
            "Quantitative emptiness: the findings table is mostly “not reported”. "
            "Keep only measured rows; move missing Latency/FLOP/Cost asks to Metric gaps. "
            "Do not scaffold empty comparison rows."
        )
    elif _setup_heavy_quant_table(blob):
        notes.append(
            "Quantitative table is setup-heavy: drop corpus size / ISL-OSL / N-runs rows; "
            "keep outcome metrics (%, ms, FLOPs, tok/s, deltas). Put sizing details in Worked example."
        )
    elif _quantitative_section_thin(blob):
        notes.append(
            "Quantitative findings: prefer exact outcome numbers from sources (%, ms, FLOPs, deltas). "
            "If none were extracted, say so once — do not invent blank metric rows."
        )
    if _counter_evidence_thin(blob):
        notes.append(
            "Counter-evidence is too thin: name the strongest contradicting source, the claim it "
            "challenges, and the task/regime in several sentences — not a one-line hedge."
        )
    if not _has_gaps_section(blob):
        notes.append(
            "Add “Uncertainties & gaps” (or legacy “What we don’t know yet”): genuine "
            "field/measurement gaps, distinct from this run’s Limitations."
        )
    if re.search(r"^##\s+Research plan\s*$", blob, re.I | re.M):
        notes.append(
            "Drop reader-facing Research plan — process narration belongs in diagnostics, not the memo."
        )
    if _redundant_hypothesis_restatement(blob):
        notes.append(
            "Structural redundancy: H1/H2 or the same failure taxonomy is restated across "
            "Analysis / Quant / Hypotheses / Findings. Argue contradictions once under "
            "Contradictions & debates; put numbers once in Quantitative findings."
        )
    if _redundant_gated_routing(blob):
        notes.append(
            "Structural redundancy: uncertainty-/entropy-gated routing is restated across many "
            "sections. State the mechanism once; elsewhere refer back — Decision rule = cutoffs only."
        )
    if query and re.search(r"scalab|scale[- ]?(?:out|up)|multi[- ]?node|kv[- ]?cache", query, re.I):
        if not re.search(r"kv[- ]?cache|memory\s+bandwidth|multi[- ]?node|hbm|cluster", blob, re.I):
            notes.append(
                "Scalability was asked but not dissected: cover KV-cache, GPU memory bandwidth, "
                "and/or multi-node behavior as its own subsection — not only latency/cost."
            )
    notes.extend(audit_memo_causal_deltas(blob))
    if re.search(r"band\s*a.{0,80}awesome|awesome.{0,80}band\s*a", blob, re.I | re.S):
        notes.append(
            "Source quality: GitHub Awesome-lists are tertiary aggregators (Band C), "
            "not Band A research papers."
        )
    if re.search(r"verified.{0,40}high|high confidence.{0,40}estimat", blob, re.I):
        notes.append(
            "Calibration: quote-matched ≠ independently measured. "
            "Author estimates / modeling assumptions must not be labeled Verified/High."
        )
    if re.search(
        r"(?:tool|schema).{0,40}(?:exceed|over|above|>|more than)\s*\d+|context.{0,30}(?:exceed|over|>)\s*\d+\s*%",
        blob,
        re.I,
    ) and "empirical cutoff" not in blob.lower():
        notes.append(
            "Decision heuristics: numeric cutoffs (tool-schema counts, % context) that are not "
            "copied from a citation belong under Engineering heuristics — or remove them."
        )
    # Same distinctive % appearing many times → template padding.
    pcts = re.findall(r"\d+(?:\.\d+)?%", blob)
    for p, count in ((p, pcts.count(p)) for p in set(pcts)):
        if count >= 5 and re.search(r"\d\.\d", p):
            notes.append(
                f"Repetition: {p} appears {count} times. State each load-bearing number once "
                "(preferably in Quantitative findings) and refer back elsewhere."
            )
            break
    return notes


def _unscoped_litm(text: str) -> bool:
    for match in LITM_RE.finditer(text or ""):
        window = text[max(0, match.start() - 90) : match.end() + 90]
        if not SCOPE_RE.search(window):
            return True
    return False


def _misattributed_corpus(text: str) -> bool:
    blob = text or ""
    if OUR_CORPUS_RE.search(blob):
        return True
    for match in PIPELINE_CORPUS_RE.finditer(blob):
        prior = blob[max(0, match.start() - 100) : match.start()]
        if re.search(
            r"based on\s*\[\d+\]|\[\d+\](?:'s|’s)|source\s*\[\d+\]|"
            r"(?:paper|survey|review)\s*\[\d+\]",
            prior,
            re.I,
        ):
            continue
        return True
    return False


def _section(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(markdown or "")
    if not match:
        return ""
    rest = (markdown or "")[match.end() :]
    nxt = re.search(r"^##\s+", rest, re.MULTILINE)
    return (rest[: nxt.start()] if nxt else rest).strip()


def _has_gaps_section(text: str) -> bool:
    return bool(
        re.search(
            r"^##\s+(Uncertainties\s*&\s*gaps|What we don't know yet)\s*$",
            text or "",
            re.I | re.M,
        )
    )


def _quantitative_emptiness(text: str) -> bool:
    section = _section(text, "Quantitative findings")
    if not section:
        return False
    not_reported = len(NOT_REPORTED_RE.findall(section))
    measured = len(
        re.findall(
            r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:ms|µs|s\b|FLOP|TFLOP|GFLOP|tok(?:ens)?/s|GB/s)",
            section,
            re.I,
        )
    )
    return not_reported >= 3 and not_reported > measured


def _quantitative_section_thin(text: str) -> bool:
    section = _section(text, "Quantitative findings")
    if not section:
        return False
    if NOT_REPORTED_RE.search(section):
        return False
    numbers = re.findall(r"\d[\d,]*(?:\.\d+)?%?", section)
    return len(numbers) < 2 and "no measured" not in section.lower() and "metric gaps" not in section.lower()


def _setup_heavy_quant_table(text: str) -> bool:
    section = _section(text, "Quantitative findings")
    if not section:
        return False
    setup = len(SETUP_TABLE_ROW_RE.findall(section))
    outcomes = len(
        re.findall(
            r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:ms|µs|s\b|FLOP|TFLOP|GFLOP|tok(?:ens)?/s|GB/s)",
            section,
            re.I,
        )
    )
    return setup >= 2 and setup > outcomes


def _redundant_gated_routing(text: str) -> bool:
    hits = list(GATED_ROUTING_RE.finditer(text or ""))
    if len(hits) < 4:
        return False
    # Count how many top-level ## sections contain a hit.
    sections_with = 0
    for heading in (
        "Executive summary",
        "Key findings",
        "Detailed analysis",
        "Contradictions & debates",
        "Decision rule",
    ):
        body = _section(text, heading)
        if body and GATED_ROUTING_RE.search(body):
            sections_with += 1
    return sections_with >= 3


def _redundant_hypothesis_restatement(text: str) -> bool:
    blob = text or ""
    # Legacy + new section names both counting as restatement homes.
    homes = 0
    for heading in (
        "Competing hypotheses",
        "Contradictions & debates",
        "Findings",
        "Key findings",
        "Detailed analysis",
        "Analysis",
    ):
        if _section(blob, heading):
            homes += 1
    if homes < 3:
        return False
    # Same distinctive H1/H2 phrasing or failure-mode count restated.
    if len(re.findall(r"\bH1\b", blob)) >= 4 and len(re.findall(r"\bH2\b", blob)) >= 4:
        return True
    if len(re.findall(r"\b19\s+failure\s+modes?\b", blob, re.I)) >= 3:
        return True
    return False


def _counter_evidence_thin(text: str) -> bool:
    matches = list(COUNTER_HEADING_RE.finditer(text or ""))
    if not matches:
        return False
    thin = 0
    for match in matches:
        rest = text[match.end() :]
        nxt = re.search(r"^#{2,4}\s+", rest, re.M)
        body = (rest[: nxt.start()] if nxt else rest).strip()
        if len(body) < 180:
            thin += 1
    return thin >= max(1, len(matches) // 2)


def append_research_critic(markdown: str, *, query: str = "") -> tuple[str, list[str]]:
    notes = audit_memo(markdown, query=query)
    if not notes:
        return markdown, []
    block = "\n".join(f"- {n}" for n in notes)
    # Fold into Limitations if present; else append.
    if re.search(r"^##\s+Limitations\s*$", markdown or "", re.I | re.M):
        return (
            re.sub(
                r"(^##\s+Limitations\s*$)",
                rf"\1\n\n### Research critic\n\n{block}",
                markdown,
                count=1,
                flags=re.I | re.M,
            ),
            notes,
        )
    return f"{markdown.rstrip()}\n\n## Limitations\n\n### Research critic\n\n{block}\n", notes
