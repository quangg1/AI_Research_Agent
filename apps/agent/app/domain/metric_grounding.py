"""Ground quantitative claims: numbers present ≠ before/after causal story.

Catches ORAgentBench-style errors where 20.59% and 35.51% both appear in the
source but mean hard-subset vs all-tasks, not baseline → improved orchestration.
"""

from __future__ import annotations

import re
from typing import Any

# "increased from 20.59% to 35.51%" / "from 20.59% to 35.51% (+14.92 pp)"
CAUSAL_DELTA_RE = re.compile(
    r"(?:increased|improved|rose|grew|jumped|lifted|boosted|raised)\s+"
    r"(?:(?:end[- ]to[- ]end\s+)?(?:task\s+|predictive\s+|diagnostic\s+)?(?:success|completion|accuracy|pass(?:\s+rate)?)\s+)?"
    r"(?:from\s+)?"
    r"(?:(?:an?\s+)?(?:unadapted\s+|adapted\s+)?baseline\s+of\s+)?"
    r"(?P<a>\d{1,3}(?:,\d{3})*(?:\.\d+)?%?)\s+to\s+(?P<b>\d{1,3}(?:,\d{3})*(?:\.\d+)?%?)"
    r"|"
    r"from\s+(?:(?:an?\s+)?(?:unadapted\s+|adapted\s+)?baseline\s+of\s+)?"
    r"(?P<a2>\d{1,3}(?:,\d{3})*(?:\.\d+)?%?)\s+to\s+(?P<b2>\d{1,3}(?:,\d{3})*(?:\.\d+)?%?)"
    r"(?:\s*\(\+?\d+(?:\.\d+)?\s*(?:percentage\s+points?|pp|%))",
    re.I,
)

# Weaker arrow form still used as a causal story in tables/bars.
ARROW_DELTA_RE = re.compile(
    r"(?P<a>\d{1,3}(?:,\d{3})*(?:\.\d+)?%)\s*(?:→|->|⇒)\s*(?P<b>\d{1,3}(?:,\d{3})*(?:\.\d+)?%)",
)

SUBSET_CUE_RE = re.compile(
    r"\b("
    r"hard(?:[- ]tasks?)?|easy(?:[- ]tasks?)?|medium(?:[- ]tasks?)?|"
    r"all\s+tasks|overall|subset|difficulty\s+split|pass\s+rate\s+is\s+only|"
    r"of\s+all\s+tasks|of\s+hard|"
    r"single[- ]dataset|cross[- ]condition|cross[- ]dataset|few[- ]shot|small[- ]sample|"
    r"few\s+shot|small\s+samples?|evaluation\s+condition|test\s+condition"
    r")\b",
    re.I,
)

# Language that would actually support an intervention / before-after reading.
INTERVENTION_CUE_RE = re.compile(
    r"\b("
    r"baseline|ablation|without\s+skills|with\s+skills|before|after\s+adding|"
    r"improved\s+by|when\s+augmented|orchestration\s+framework|"
    r"compared\s+to\s+(?:the\s+)?baseline|vs\.?\s+baseline|versus\s+baseline|"
    r"control\s+condition|treatment"
    r")\b",
    re.I,
)


def _norm_num(raw: str) -> str:
    return (raw or "").replace(",", "").rstrip("%").strip()


def extract_causal_deltas(text: str) -> list[dict[str, str]]:
    """Pull claimed before→after pairs from memo/claim prose."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern in (CAUSAL_DELTA_RE, ARROW_DELTA_RE):
        for match in pattern.finditer(text or ""):
            a = match.groupdict().get("a") or match.groupdict().get("a2") or ""
            b = match.groupdict().get("b") or match.groupdict().get("b2") or ""
            if not a or not b:
                continue
            key = (_norm_num(a), _norm_num(b))
            if key in seen or key[0] == key[1]:
                continue
            seen.add(key)
            out.append({"a": a, "b": b, "span": match.group(0)[:120]})
    return out


def _windows_for_number(source: str, num: str, radius: int = 140) -> list[str]:
    needle = _norm_num(num)
    if not needle or not source:
        return []
    windows: list[str] = []
    for match in re.finditer(re.escape(needle), source):
        # Prefer % forms when present in source next to the digits.
        start = max(0, match.start() - radius)
        end = min(len(source), match.end() + radius)
        windows.append(source[start:end])
    return windows


def assess_causal_delta(claim_text: str, source_text: str) -> dict[str, Any] | None:
    """If claim asserts A→B improvement, check the source actually supports that relation.

    Returns None when the claim has no causal delta pattern.
    """
    deltas = extract_causal_deltas(claim_text)
    if not deltas:
        return None
    source = source_text or ""
    if len(source) < 40:
        return {
            "status": "source_missing",
            "note": "Causal % delta claimed but source text was too short to verify the comparison.",
            "deltas": deltas,
        }

    for delta in deltas:
        a_wins = _windows_for_number(source, delta["a"])
        b_wins = _windows_for_number(source, delta["b"])
        if not a_wins or not b_wins:
            return {
                "status": "wrong_number",
                "note": (
                    f"Causal claim uses {_norm_num(delta['a'])}→{_norm_num(delta['b'])} "
                    "but at least one figure is missing from the retrieved source."
                ),
                "deltas": deltas,
            }
        joined = " ".join(a_wins + b_wins)
        has_subset = bool(SUBSET_CUE_RE.search(joined))
        has_intervention = bool(INTERVENTION_CUE_RE.search(joined))
        # Both numbers appear with subset/difficulty language and no intervention cue
        # → classic hard-vs-all misread.
        if has_subset and not has_intervention:
            return {
                "status": "wrong_causal",
                "note": (
                    f"Source mentions {_norm_num(delta['a'])} and {_norm_num(delta['b'])} "
                    "as different task subsets/conditions (e.g. hard vs all), not as a "
                    "before→after gain from adding orchestration. Do not invent that causal story."
                ),
                "deltas": deltas,
            }
        # Numbers present but source never states an increase from A to B.
        claim_span = (delta.get("span") or "").lower()
        if "increased" in claim_span or "improved" in claim_span or "from" in claim_span:
            if not has_intervention and not re.search(
                rf"{re.escape(_norm_num(delta['a']))}.{{0,80}}(?:to|→).{{0,40}}{re.escape(_norm_num(delta['b']))}",
                source,
                re.I | re.S,
            ):
                return {
                    "status": "wrong_causal",
                    "note": (
                        f"Both {_norm_num(delta['a'])} and {_norm_num(delta['b'])} appear in the source, "
                        "but the source does not state that one improved into the other via an intervention. "
                        "Report each metric with its own condition; leave comparison_baseline empty if unset."
                    ),
                    "deltas": deltas,
                }
    return {
        "status": "ok",
        "note": "Causal delta numbers found with plausible comparison cues in source windows.",
        "deltas": deltas,
    }


def audit_memo_causal_deltas(memo: str) -> list[str]:
    """Memo-level warnings when causal % stories appear (source check is separate)."""
    deltas = extract_causal_deltas(memo or "")
    if not deltas:
        return []
    examples = ", ".join(f"{d['a']}→{d['b']}" for d in deltas[:3])
    return [
        "Causal percentage stories detected ("
        + examples
        + "). Each must name the task subset/condition and a source sentence that "
        "explicitly compares the same metric under two interventions — not hard vs all, "
        "or two unrelated rows. If the source lacks that comparison, write "
        "'not explicitly compared in source' and do not invent +pp / relative %."
    ]


SCOPE_NARROW_RE = re.compile(
    r"\b("
    r"HAR|human\s+activity|wearable|sensor|2\.?\d?\s*M\s*(?:param|parameters)|"
    r"million\s+parameters|CPU[- ]based|simplified\s+implementation|"
    r"single\s+transformer\s+block|encoder\s+block|MAE\s+backbone"
    r")\b",
    re.I,
)
SCOPE_BROAD_RE = re.compile(
    r"\b("
    r"70B|65B|48\s*GB|LLM|large\s+language\s+model|domain[- ]specific\s+adaptation|"
    r"hardware\s+memory\s+measurement|static\s+(?:base\s+)?weight\s+memory"
    r")\b",
    re.I,
)


def assess_scope_overgeneralization(claim_text: str, source_text: str) -> dict | None:
    """Flag when a narrow-experiment number is stated as a universal LLM systems fact."""
    claim = claim_text or ""
    source = source_text or ""
    if not claim.strip() or len(source) < 40:
        return None
    if not SCOPE_BROAD_RE.search(claim):
        return None
    if not SCOPE_NARROW_RE.search(source):
        return None
    # Broad claim + narrow source, and claim omits the narrow qualifier.
    if SCOPE_NARROW_RE.search(claim):
        return None
    return {
        "status": "scope_bleed",
        "note": (
            "Numeric claim is phrased as a general LLM/systems fact, but the cited source "
            "measures a narrow setup (e.g. small HAR encoder / CPU simplified impl). "
            "Keep the source scope qualifier in the memo sentence."
        ),
    }


def audit_memo_scope_bleed(memo: str) -> list[str]:
    if not memo:
        return []
    if SCOPE_BROAD_RE.search(memo) and re.search(r"\b10\.06\s*MB\b|\b6\.22\s*MB\b|\b0\.01\s*MB\b", memo):
        return [
            "Memo cites micro-block memory figures (e.g. 10.06 MB / 6.22 MB) alongside "
            "70B/48GB-scale language. Keep each number scoped to the experiment that measured it; "
            "do not present HAR/CPU block MB as a universal QLoRA LLM fact."
        ]
    return []


# --- Hard claim <-> span grounding (Claude gate) ------------------------------
# Factual claims (numeric OR prose) must map to 1-2 contiguous source sentences.
# Extra mechanism/causal wording absent from that span is ungrounded elaboration.
# Inferred / speculative / recommendation claims are exempt (marked separately).

SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\!\?])\s+|\n+")
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "to", "of", "in", "on", "for", "with", "from", "by", "as",
    "at", "is", "are", "was", "were", "be", "been", "being", "it", "its", "into",
    "after", "before", "over", "under", "between", "within", "via", "using",
    "used", "use", "we", "our", "their", "they", "his", "her", "not", "no",
    "vs", "versus", "per", "such", "may", "can", "could", "would", "should",
    "also", "more", "most", "less", "very", "highly", "based", "when", "while",
    "during", "about", "across", "through", "only", "both", "each", "all",
    "has", "have", "had", "does", "did", "do", "will", "shall", "must",
}

MECHANISM_PHRASE_RE = re.compile(
    r"\b("
    r"gradient\s+sensitivity|layer[- ]wise|dynamically\s+allocat\w*|"
    r"unadapted\s+baseline|baseline\s+of|before[- ]after|"
    r"system\s+1|system\s+2|importance\s+scor\w*|multi[- ]model\s+role[- ]play\w*|"
    r"attention\s+routing|token[- ]level\s+gating|neural\s+architecture\s+search"
    r")\b",
    re.I,
)

NUMBER_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+")
SKIP_KINDS = {"inferred", "speculative", "recommendation", "inference"}


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_SPLIT_RE.split(text or "") if p and p.strip()]
    return parts or ([text.strip()] if (text or "").strip() else [])


def load_bearing_numeric_tokens(text: str) -> list[str]:
    """Distinct numeric tokens that are likely claim-bearing (skip tiny ints / years)."""
    out: list[str] = []
    seen: set[str] = set()
    for match in NUMBER_TOKEN_RE.finditer(text or ""):
        raw = match.group(0)
        norm = raw.replace(",", "")
        if norm.isdigit():
            n = int(norm)
            if n < 10 or 1900 <= n <= 2035:
                continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _content_tokens(text: str) -> set[str]:
    toks = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", (text or "").lower())
    return {t for t in toks if t not in STOPWORDS}


def _find_span_window(sentences: list[str], numbers: list[str]) -> tuple[str, int, int] | None:
    """Return (span_text, start_idx, end_idx_inclusive) covering all numbers in <=2 sentences."""
    if not sentences or not numbers:
        return None
    lowered = [s.lower() for s in sentences]
    for i, s in enumerate(lowered):
        if all(n.lower() in s or n in s for n in numbers):
            return sentences[i], i, i
    for i in range(len(lowered) - 1):
        joined = lowered[i] + " " + lowered[i + 1]
        if all(n.lower() in joined or n in joined for n in numbers):
            return sentences[i] + " " + sentences[i + 1], i, i + 1
    return None


def _best_semantic_window(sentences: list[str], claim_toks: set[str]) -> tuple[str, set[str], float] | None:
    """Pick the 1-2 sentence window with strongest content-token overlap."""
    if not sentences or not claim_toks:
        return None
    best: tuple[str, set[str], float] | None = None
    candidates: list[str] = list(sentences)
    for i in range(len(sentences) - 1):
        candidates.append(sentences[i] + " " + sentences[i + 1])
    for span in candidates:
        span_toks = _content_tokens(span)
        overlap = claim_toks & span_toks
        score = len(overlap) / max(1, len(claim_toks))
        if best is None or score > best[2] or (score == best[2] and len(overlap) > len(best[1])):
            best = (span, overlap, score)
    return best


def _mechanism_failures(claim: str, span: str) -> list[str]:
    return [m.group(0) for m in MECHANISM_PHRASE_RE.finditer(claim) if m.group(0).lower() not in span.lower()]


def _finalize_span(
    *,
    claim: str,
    span: str,
    numbers: list[str],
    claim_toks: set[str],
    overlap: set[str],
    quote: str,
    numeric: bool,
    min_overlap: int,
    min_ratio: float,
) -> dict[str, Any]:
    quote_ok = bool(quote) and quote.strip().lower() in span.lower()
    bad = _mechanism_failures(claim, span)
    if bad:
        return {
            "status": "ungrounded",
            "note": (
                "Claim adds mechanism/causal wording ("
                + ", ".join(bad[:3])
                + ") that is not present in the 1-2 sentence supporting source span."
            ),
            "numbers": numbers,
            "span": span[:400],
            "mode": "numeric" if numeric else "semantic",
        }
    ratio = len(overlap) / max(1, len(claim_toks))
    if not quote_ok and (len(overlap) < min_overlap or ratio < min_ratio):
        return {
            "status": "ungrounded",
            "note": (
                "Claim wording is not supported by any 1-2 contiguous source sentences "
                "(insufficient lexical alignment with the cited span)."
            ),
            "numbers": numbers,
            "span": span[:400],
            "mode": "numeric" if numeric else "semantic",
        }
    return {
        "status": "ok",
        "note": (
            "Numeric claim grounded in a 1-2 sentence source span."
            if numeric
            else "Prose claim grounded in a 1-2 sentence source span."
        ),
        "numbers": numbers,
        "span": span[:400],
        "overlap_terms": sorted(overlap)[:12],
        "mode": "numeric" if numeric else "semantic",
    }


def assess_claim_span_grounding(
    claim_text: str,
    source_text: str,
    *,
    quote: str = "",
    kind: str = "",
) -> dict[str, Any] | None:
    """Hard gate: factual claims must ground in a 1-2 sentence source span.

    Covers numeric and non-numeric prose. Returns None when the gate is N/A
    (empty claim, or explicitly inferred/speculative/recommendation).
    """
    claim = (claim_text or "").strip()
    source = (source_text or "").strip()
    if not claim:
        return None
    kind_l = (kind or "").strip().lower()
    if kind_l in SKIP_KINDS:
        return None

    numbers = load_bearing_numeric_tokens(claim)
    claim_toks = _content_tokens(claim)
    if len(source) < 40:
        return {
            "status": "ungrounded",
            "note": "Claim lacks a retrieved source span long enough to ground it.",
            "numbers": numbers,
            "span": "",
            "mode": "numeric" if numbers else "semantic",
        }

    sentences = split_sentences(source)

    # --- Numeric path ---------------------------------------------------------
    if numbers:
        window = _find_span_window(sentences, numbers)
        if window is None:
            missing = [n for n in numbers if n not in source.replace(",", "")]
            if missing:
                return {
                    "status": "ungrounded",
                    "note": (
                        "Load-bearing number(s) "
                        + ", ".join(missing[:4])
                        + " are absent from the cited source span."
                    ),
                    "numbers": numbers,
                    "span": "",
                    "mode": "numeric",
                }
            return {
                "status": "ungrounded",
                "note": (
                    "Numbers "
                    + ", ".join(numbers[:4])
                    + " appear in the source but not together in any 1-2 contiguous sentences. "
                    "Do not stitch distant figures into one claim."
                ),
                "numbers": numbers,
                "span": "",
                "mode": "numeric",
            }
        span, _i, _j = window
        overlap = claim_toks & _content_tokens(span)
        return _finalize_span(
            claim=claim,
            span=span,
            numbers=numbers,
            claim_toks=claim_toks,
            overlap=overlap,
            quote=quote,
            numeric=True,
            min_overlap=2 if len(claim_toks) >= 4 else 1,
            min_ratio=0.2,
        )

    # --- Semantic prose path --------------------------------------------------
    if len(claim_toks) < 2:
        # Too thin to hard-gate (titles / fragments).
        return None

    # If a quote is provided and present, prefer the local 1-2 sentence neighborhood.
    if quote and quote.strip().lower() in source.lower():
        q = quote.strip()
        idx = source.lower().find(q.lower())
        # gather sentences overlapping the quote region
        pos = 0
        hit_idxs: list[int] = []
        for i, sent in enumerate(sentences):
            start = source.find(sent, pos)
            if start < 0:
                start = pos
            end = start + len(sent)
            pos = end
            if start <= idx <= end or start <= idx + len(q) <= end or (idx <= start and end <= idx + len(q)):
                hit_idxs.append(i)
        if hit_idxs:
            i0, i1 = hit_idxs[0], hit_idxs[-1]
            if i1 - i0 > 1:
                i1 = i0 + 1
            span = " ".join(sentences[i0 : i1 + 1])
            overlap = claim_toks & _content_tokens(span)
            return _finalize_span(
                claim=claim,
                span=span,
                numbers=[],
                claim_toks=claim_toks,
                overlap=overlap,
                quote=quote,
                numeric=False,
                min_overlap=2 if len(claim_toks) >= 5 else 1,
                min_ratio=0.25,
            )

    best = _best_semantic_window(sentences, claim_toks)
    if best is None:
        return {
            "status": "ungrounded",
            "note": "No source sentence window could be aligned to the claim.",
            "numbers": [],
            "span": "",
            "mode": "semantic",
        }
    span, overlap, _score = best
    return _finalize_span(
        claim=claim,
        span=span,
        numbers=[],
        claim_toks=claim_toks,
        overlap=overlap,
        quote=quote,
        numeric=False,
        min_overlap=max(2, min(4, len(claim_toks) // 3 or 2)),
        min_ratio=0.34,
    )


# --- Subject / scale / topic claim↔evidence gates (Kiln memo quality) ---------
# Catch live-memo failure modes where a retrieved paper is "about LoRA/QLoRA"
# but does not support the *asked* subject (7B VRAM footprints) — e.g. a 1.5B
# profiling paper, a mental-health DPO/ORPO/KTO paper, or an email-QA abstention
# paper driving Key findings / Detailed analysis sections.

MODEL_SCALE_RE = re.compile(
    r"\b(?P<n>\d+(?:\.\d+)?)\s*[Bb](?:illion)?(?:\s*(?:param(?:eter)?s?|model))?\b"
)

MEMORY_QUERY_RE = re.compile(
    r"\b("
    r"VRAM|peak\s+(?:GPU\s+)?memory|memory\s+footprint|training\s+memory|"
    r"GPU\s+memory|GB\s+VRAM|memory\s+(?:usage|consumption|requirement)s?|"
    r"LoRA\s+vs\.?\s+QLoRA|QLoRA\s+vs\.?\s+LoRA"
    r")\b",
    re.I,
)

MEMORY_CLAIM_RE = re.compile(
    r"\b("
    r"VRAM|peak\s+(?:GPU\s+)?memory|memory\s+footprint|training\s+memory|"
    r"GPU\s+memory|(?:\d+(?:\.\d+)?)\s*(?:GB|MB)\b|tok(?:ens)?/s|throughput|"
    r"NF4|bitsandbytes|paged\s+optimizer|static\s+(?:base\s+)?weight"
    r")\b",
    re.I,
)

MEMORY_EVIDENCE_RE = re.compile(
    r"\b("
    r"bitsandbytes|NF4|paged\s+optimizer|weight\s+memory|"
    r"(?:peak\s+)?(?:GPU\s+)?(?:VRAM|memory)\s+(?:of\s+)?(?:~?\d|footprint|usage|consumption)|"
    r"(?:\d+(?:\.\d+)?)\s*(?:GB|MB)\b"
    r")\b",
    re.I,
)

# Negation / absence cues — mentioning VRAM only to say it was *not* measured
# must not count as on-topic memory evidence.
MEMORY_ABSENCE_RE = re.compile(
    r"\b("
    r"no\s+(?:GPU\s+)?(?:VRAM|memory)|(?:VRAM|memory).{0,40}(?:not\s+(?:reported|measured|provided)|absent)|"
    r"does\s+not\s+(?:report|measure|provide).{0,40}(?:VRAM|memory|footprint)|"
    r"without\s+(?:reporting\s+)?(?:VRAM|memory\s+footprint)"
    r")\b",
    re.I,
)

PREFERENCE_TOPIC_RE = re.compile(
    r"\b("
    r"DPO|ORPO|KTO|RLHF|preference\s+optim\w*|preference\s+alignment|"
    r"class[- ]rebalanc\w*|chosen\s+vs\.?\s+rejected|pairwise\s+preference"
    r")\b",
    re.I,
)

CLINICAL_TOPIC_RE = re.compile(
    r"\b("
    r"mental[- ]health|clinical\s+text|depression|anxiety|therapy|"
    r"patient\s+(?:dialog|dialogue|message)|psychiatric"
    r")\b",
    re.I,
)

ABSTENTION_TOPIC_RE = re.compile(
    r"\b("
    r"abstention|unanswerable|email\s+QA|email\s+question\s+answering|"
    r"abstain(?:ing|s|ed)?\s+(?:from\s+)?answering|abstention\s+accuracy"
    r")\b",
    re.I,
)

# Subsection headings that should not be synthesized for a VRAM/memory query.
OFF_SCOPE_SECTION_HEADING_RE = re.compile(
    r"^###\s+("
    r"Class[- ]rebalanced\s+preference\s+adaptation|"
    r"Preference[- ]based\s+optimization\s+dynamics|"
    r".{0,60}abstention.{0,40}|"
    r".{0,40}preference\s+(?:adaptation|optim|alignment).{0,40}"
    r")\s*$",
    re.I | re.M,
)


def extract_model_scales(text: str) -> set[str]:
    """Normalize model-size mentions to tokens like '7b', '1.5b'."""
    out: set[str] = set()
    for match in MODEL_SCALE_RE.finditer(text or ""):
        raw = match.group("n")
        try:
            val = float(raw)
        except ValueError:
            continue
        # Skip years / unrelated small ints that aren't model sizes.
        if val < 0.1 or val > 1000:
            continue
        if val == int(val):
            out.add(f"{int(val)}b")
        else:
            # Keep one decimal when present (1.5B).
            out.add(f"{val:.1f}".rstrip("0").rstrip(".") + "b")
    return out


def is_memory_footprint_query(query: str) -> bool:
    q = query or ""
    if MEMORY_QUERY_RE.search(q):
        return True
    # LoRA vs QLoRA + (7B|memory|VRAM|GB) without explicit "vs" still counts.
    if re.search(r"\b(?:LoRA|QLoRA)\b", q, re.I) and re.search(
        r"\b(?:VRAM|memory|footprint|\d+(?:\.\d+)?\s*[Bb])\b", q, re.I
    ):
        return True
    return False


def source_topic_tags(source_text: str) -> set[str]:
    text = source_text or ""
    tags: set[str] = set()
    if PREFERENCE_TOPIC_RE.search(text):
        tags.add("preference")
    if CLINICAL_TOPIC_RE.search(text):
        tags.add("clinical")
    if ABSTENTION_TOPIC_RE.search(text):
        tags.add("abstention")
    # Positive memory evidence only — ignore "VRAM not reported" disclaimers.
    if MEMORY_EVIDENCE_RE.search(text) and not (
        MEMORY_ABSENCE_RE.search(text) and not re.search(
            r"\d+(?:\.\d+)?\s*(?:GB|MB)\b", text, re.I
        )
    ):
        tags.add("memory")
    elif re.search(r"\d+(?:\.\d+)?\s*(?:GB|MB)\b", text, re.I) and MEMORY_EVIDENCE_RE.search(text):
        tags.add("memory")
    return tags


def assess_subject_scale_scope(
    claim_text: str,
    source_text: str,
    query: str = "",
) -> dict[str, Any] | None:
    """Forbid attributing a source's numbers to a model scale the source never measured.

    Classic failure: arXiv profiling Qwen2.5-1.5B on RTX 4060 cited as 7B VRAM/throughput.
    """
    claim = claim_text or ""
    source = source_text or ""
    if not claim.strip() or len(source) < 40:
        return None

    claim_scales = extract_model_scales(claim)
    source_scales = extract_model_scales(source)
    query_scales = extract_model_scales(query)

    # Target scale: what the claim asserts, preferring the query's asked scale.
    target = (claim_scales & query_scales) or (claim_scales if claim_scales else set())
    if query_scales and not claim_scales:
        # "peak VRAM … for 7B fine-tuning" sometimes omits "7B" next to the number
        # but the surrounding claim block still attributes to the asked scale.
        if MEMORY_CLAIM_RE.search(claim) and query_scales:
            target = set(query_scales)

    if not target:
        return None
    if not source_scales:
        # Source never states a model scale — cannot prove mismatch; leave to span gate.
        return None

    # Source supports the claimed scale if it mentions it.
    if target & source_scales:
        return None

    # Mismatch only when claim carries load-bearing numbers (or memory metrics)
    # that look like quantitative attribution, not a soft mention.
    numbers = load_bearing_numeric_tokens(claim)
    if not numbers and not MEMORY_CLAIM_RE.search(claim):
        return None

    # Prefer flagging when at least one claimed number also appears in the source
    # (misattribution of real figures) OR claim explicitly names the wrong scale.
    source_flat = source.replace(",", "")
    number_overlap = [n for n in numbers if n in source_flat]
    if not number_overlap and not (claim_scales - source_scales):
        return None

    src_list = ", ".join(sorted(source_scales))
    tgt_list = ", ".join(sorted(target))
    return {
        "status": "scale_mismatch",
        "note": (
            f"Claim attributes quantitative results to model scale {tgt_list}, but the "
            f"cited source only measures {src_list}. Do not transplant those numbers "
            "onto the asked scale; keep each figure scoped to the experiment that produced it."
        ),
        "claim_scales": sorted(claim_scales),
        "source_scales": sorted(source_scales),
        "query_scales": sorted(query_scales),
    }


def assess_subject_topic_scope(
    claim_text: str,
    source_text: str,
    query: str = "",
) -> dict[str, Any] | None:
    """Mark preference / clinical / abstention sources as off-scope for memory queries.

    Classic failures: mental-health DPO/ORPO/KTO LoRA paper and email-QA abstention
    paper (~0.998) driving VRAM / LoRA-vs-QLoRA memory sections.
    """
    if not is_memory_footprint_query(query):
        return None
    claim = claim_text or ""
    source = source_text or ""
    if not claim.strip() or len(source) < 40:
        return None

    tags = source_topic_tags(source)
    off_tags = tags & {"preference", "clinical", "abstention"}
    if not off_tags:
        return None

    # If the source also has real memory/VRAM evidence, allow memory claims that
    # are actually grounded there — only block when memory evidence is absent.
    has_memory_evidence = "memory" in tags
    claim_is_memory = bool(MEMORY_CLAIM_RE.search(claim))
    claim_is_off = bool(
        PREFERENCE_TOPIC_RE.search(claim)
        or CLINICAL_TOPIC_RE.search(claim)
        or ABSTENTION_TOPIC_RE.search(claim)
    )

    # Case A: memory/VRAM claim cited to an off-topic paper with no memory spans.
    if claim_is_memory and not has_memory_evidence:
        return {
            "status": "topic_mismatch",
            "note": (
                "Query asks for VRAM/memory footprints, but the cited source is primarily "
                f"{', '.join(sorted(off_tags))} (preference/clinical/abstention) and does not "
                "report on-topic memory/VRAM evidence for the asked comparison. "
                "Do not use it to drive Key findings / Detailed analysis / Decision rule."
            ),
            "tags": sorted(tags),
        }

    # Case B: claim itself is preference/abstention prose — off-scope for a memory query
    # even if the source mentions LoRA as a training tool.
    if claim_is_off and not claim_is_memory:
        return {
            "status": "topic_mismatch",
            "note": (
                "Claim concerns "
                f"{', '.join(sorted(off_tags))} rather than the asked VRAM/memory comparison. "
                "Keep Hu/Dettmers-style primaries when they actually report memory; "
                "do not synthesize preference/abstention sections from this cite alone."
            ),
            "tags": sorted(tags),
        }

    return None


def audit_memo_subject_scope(memo: str, query: str = "") -> list[str]:
    """Memo-level warnings for off-scope sections / scale bleed on memory queries."""
    if not memo:
        return []
    notes: list[str] = []
    if is_memory_footprint_query(query or memo):
        if OFF_SCOPE_SECTION_HEADING_RE.search(memo):
            notes.append(
                "Off-scope section(s) under Detailed analysis (preference adaptation / "
                "preference optimization / abstention) for a VRAM/memory query. "
                "Those cites must not drive Key findings, Detailed analysis, or Decision rule."
            )
        # 1.5B-only figures attributed next to 7B language (heuristic).
        if extract_model_scales(query) and re.search(
            r"\b1\.5\s*[Bb]\b.{0,120}\b7\s*[Bb]\b|\b7\s*[Bb]\b.{0,120}\b1\.5\s*[Bb]\b",
            memo,
            re.I | re.S,
        ):
            notes.append(
                "Scale bleed: memo juxtaposes 1.5B experiment figures with 7B attributions. "
                "Keep each VRAM/throughput number scoped to the model size actually measured."
            )
        if re.search(r"\b0\.998\b", memo) and re.search(
            r"\babstention\b", memo, re.I
        ):
            notes.append(
                "Abstention-accuracy figures (e.g. 0.998) appear in a memory/VRAM memo — "
                "they are off-topic for LoRA vs QLoRA peak-memory comparison."
            )
    return notes


def demote_off_scope_memo_content(
    body: str,
    *,
    query: str,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """Soft-demote off-topic subsections and scale-mismatched quantitative lines.

    Does NOT strip [n] markers inside ## References / ## Source quality (avoids ****).
    """
    if not body or not is_memory_footprint_query(query):
        return body or "", []

    flags: list[str] = []
    text = body

    # 1) Drop ### preference / abstention subsections under Detailed analysis.
    def _drop_off_scope_subsections(md: str) -> tuple[str, int]:
        parts = re.split(r"(^###\s+.+$)", md, flags=re.M)
        if len(parts) <= 1:
            return md, 0
        out: list[str] = []
        dropped = 0
        i = 0
        while i < len(parts):
            part = parts[i]
            if re.match(r"^###\s+", part):
                heading = part
                body_chunk = parts[i + 1] if i + 1 < len(parts) else ""
                if OFF_SCOPE_SECTION_HEADING_RE.match(heading.strip()) or (
                    PREFERENCE_TOPIC_RE.search(heading)
                    or ABSTENTION_TOPIC_RE.search(heading)
                ):
                    # Only replace this ### body — stop before the next ##/### heading
                    # so ## References / later sections are not swallowed.
                    nxt = re.search(r"^##\s+", body_chunk, re.M)
                    rest = body_chunk[nxt.start():] if nxt else ""
                    out.append(heading)
                    out.append(
                        "\n> **Demoted (off-scope for query):** this subsection was "
                        "synthesized from preference/clinical/abstention sources that do not "
                        "ground the asked VRAM/memory comparison.\n\n"
                    )
                    if rest:
                        out.append(rest)
                    dropped += 1
                    i += 2
                    continue
                out.append(heading)
                out.append(body_chunk)
                i += 2
                continue
            out.append(part)
            i += 1
        return "".join(out), dropped

    text, n_drop = _drop_off_scope_subsections(text)
    if n_drop:
        flags.append(f"off_scope_sections_demoted_{n_drop}")

    # 2) Strip Key findings / Decision rule lines that fail scale or topic gates
    #    against the cited evidence (prose only — protected sections untouched).
    if evidence and citations:
        by_n = {}
        for c in citations:
            try:
                by_n[int(c["n"])] = c
            except (KeyError, TypeError, ValueError):
                continue
        by_url = {
            (ev.get("url") or "").strip().rstrip("/").lower(): ev
            for ev in evidence
            if ev.get("url")
        }

        def _source_for_line(line: str) -> str:
            blobs: list[str] = []
            for match in CITE_MARKER_RE.finditer(line):
                for piece in match.group(1).split(","):
                    digits = re.match(r"\s*(\d+)", piece)
                    if not digits:
                        continue
                    cite = by_n.get(int(digits.group(1))) or {}
                    url = (cite.get("url") or "").strip().rstrip("/").lower()
                    ev = by_url.get(url) or {}
                    blob = (
                        ev.get("full_text")
                        or ev.get("text")
                        or ev.get("content")
                        or ev.get("snippet")
                        or cite.get("snippet")
                        or ""
                    )
                    if blob:
                        blobs.append(blob)
            return "\n".join(blobs)

        protected = {"source quality", "references"}

        def _scrub_section_lines(md: str) -> tuple[str, int]:
            sections = re.split(r"(^##\s+.+$)", md, flags=re.M)
            out_parts: list[str] = []
            removed = 0
            in_protected = False
            target_section = False
            for part in sections:
                if re.match(r"^##\s+", part):
                    name = re.sub(r"^##\s+", "", part).strip().lower()
                    in_protected = any(p in name for p in protected)
                    target_section = name in {
                        "key findings",
                        "decision rule",
                        "executive summary",
                        "at a glance",
                        "comparison",
                    } or name.startswith("detailed analysis")
                    out_parts.append(part)
                    continue
                if in_protected or not target_section:
                    out_parts.append(part)
                    continue
                kept_lines: list[str] = []
                for line in part.splitlines(keepends=True):
                    raw = line.rstrip("\n")
                    if not raw.strip() or not CITE_MARKER_RE.search(raw):
                        kept_lines.append(line)
                        continue
                    src = _source_for_line(raw)
                    if len(src) < 40:
                        kept_lines.append(line)
                        continue
                    scale = assess_subject_scale_scope(raw, src, query=query)
                    topic = assess_subject_topic_scope(raw, src, query=query)
                    if (scale and scale.get("status") == "scale_mismatch") or (
                        topic and topic.get("status") == "topic_mismatch"
                    ):
                        removed += 1
                        continue
                    kept_lines.append(line)
                out_parts.append("".join(kept_lines))
            return "".join(out_parts), removed

        text, n_removed = _scrub_section_lines(text)
        if n_removed:
            flags.append(f"off_scope_claim_lines_stripped_{n_removed}")

    return text, flags


# Citation marker used by demote_off_scope_memo_content (kept local to avoid
# importing report_integrity → cycle).
CITE_MARKER_RE = re.compile(r"\[(\d+(?:\s+[A-Za-z]+)?(?:\s*,\s*\d+(?:\s+[A-Za-z]+)?)*)\]")

