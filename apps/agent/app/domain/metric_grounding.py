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
    r"(?:(?:end[- ]to[- ]end\s+)?(?:task\s+)?(?:success|completion|pass(?:\s+rate)?)\s+)?"
    r"(?:from\s+)?"
    r"(?P<a>\d{1,3}(?:,\d{3})*(?:\.\d+)?%?)\s+to\s+(?P<b>\d{1,3}(?:,\d{3})*(?:\.\d+)?%?)"
    r"|"
    r"from\s+(?P<a2>\d{1,3}(?:,\d{3})*(?:\.\d+)?%?)\s+to\s+(?P<b2>\d{1,3}(?:,\d{3})*(?:\.\d+)?%?)"
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
    r"of\s+all\s+tasks|of\s+hard"
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
