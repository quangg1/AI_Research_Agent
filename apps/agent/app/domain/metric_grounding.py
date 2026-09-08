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


# --- Hard claim ↔ span grounding (Claude gate) ---------------------------------
# Every load-bearing numeric claim must map to 1-2 contiguous source sentences
# that actually contain those numbers. Extra mechanism/causal wording that is
# absent from that span is treated as ungrounded elaboration.

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
}

MECHANISM_PHRASE_RE = re.compile(
    r"\b("
    r"gradient\s+sensitivity|layer[- ]wise|dynamically\s+allocat\w*|"
    r"unadapted\s+baseline|baseline\s+of|before[- ]after|"
    r"system\s+1|system\s+2|importance\s+scor\w*|multi[- ]model\s+role[- ]play\w*"
    r")\b",
    re.I,
)

NUMBER_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+")


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
    """Return (span_text, start_idx, end_idx_inclusive) covering all numbers in ≤2 sentences."""
    if not sentences or not numbers:
        return None
    lowered = [s.lower() for s in sentences]
    # Prefer single sentence containing all numbers.
    for i, s in enumerate(lowered):
        if all(n.lower() in s or n in s for n in numbers):
            return sentences[i], i, i
    # Then two adjacent sentences.
    for i in range(len(lowered) - 1):
        joined = lowered[i] + " " + lowered[i + 1]
        if all(n.lower() in joined or n in joined for n in numbers):
            return sentences[i] + " " + sentences[i + 1], i, i + 1
    return None


def assess_claim_span_grounding(
    claim_text: str,
    source_text: str,
    *,
    quote: str = "",
) -> dict[str, Any] | None:
    """Hard gate for numeric claims: require a 1-2 sentence supporting span.

    Returns None when the claim has no load-bearing numbers (gate N/A).
    """
    claim = (claim_text or "").strip()
    source = (source_text or "").strip()
    numbers = load_bearing_numeric_tokens(claim)
    if not numbers:
        return None
    if len(source) < 40:
        return {
            "status": "ungrounded",
            "note": "Numeric claim lacks a retrieved source span long enough to ground the figures.",
            "numbers": numbers,
            "span": "",
        }

    sentences = split_sentences(source)
    window = _find_span_window(sentences, numbers)
    if window is None:
        # Numbers may still appear somewhere, just not co-located in 1-2 sentences.
        present = [n for n in numbers if n in source.replace(",", "")]
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
        }

    span, _i, _j = window
    claim_toks = _content_tokens(claim)
    span_toks = _content_tokens(span)
    overlap = claim_toks & span_toks
    quote_ok = bool(quote) and quote.strip().lower() in span.lower()

    # Mechanism / causal elaboration must appear in the supporting span (check first).
    mechs = [m.group(0) for m in MECHANISM_PHRASE_RE.finditer(claim)]
    bad = [m for m in mechs if m.lower() not in span.lower()]
    if bad:
        return {
            "status": "ungrounded",
            "note": (
                "Claim adds mechanism/causal wording ("
                + ", ".join(bad[:3])
                + ") that is not present in the 1-2 sentence source span that holds the numbers."
            ),
            "numbers": numbers,
            "span": span[:400],
        }

    # Need some lexical support beyond bare numbers, unless a verified quote sits in the span.
    min_overlap = 2 if len(claim_toks) >= 4 else 1
    if not quote_ok and len(overlap) < min_overlap:
        return {
            "status": "ungrounded",
            "note": (
                "A 1-2 sentence source window contains the numbers, but the claim's wording "
                "does not align with that span (possible stitched/over-interpreted finding)."
            ),
            "numbers": numbers,
            "span": span[:400],
        }

    return {
        "status": "ok",
        "note": "Numeric claim grounded in a 1-2 sentence source span.",
        "numbers": numbers,
        "span": span[:400],
        "overlap_terms": sorted(overlap)[:12],
    }

