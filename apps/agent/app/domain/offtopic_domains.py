"""Shared off-topic / OOD domain detectors for ranking and memo polish.

Keep one HAR/sensor regex for LLM FT queries so ranking, extract, and
memo_structure all demote the same poison papers — not only the quant table.
"""

from __future__ import annotations

import re

HAR_SENSOR_RE = re.compile(
    r"\b(?:HHAR|UCI[-_ ]?HAR|\bHAR\b|Human\s+Activity\s+Recognition|"
    r"accelerometer|gyroscope|wearable\s+sensor|activity\s+recognition)\b",
    re.I,
)

LLM_FT_QUERY_RE = re.compile(
    r"\b(?:LoRA|QLoRA|fine[- ]?tun(?:ing|e)?|language\s+model|\bLLM\b|PEFT)\b",
    re.I,
)


def is_llm_ft_query(query: str) -> bool:
    """True when the user question is about LLM fine-tuning / LoRA / PEFT."""
    return bool(LLM_FT_QUERY_RE.search(query or ""))


def is_har_sensor_text(text: str) -> bool:
    return bool(HAR_SENSOR_RE.search(text or ""))


def _evidence_blob(ev: dict) -> str:
    return " ".join(
        str(ev.get(k) or "")
        for k in ("title", "snippet", "quote", "full_text", "text", "content")
    )


def evidence_is_har_sensor_ood(ev: dict, query: str = "") -> bool:
    """HAR/sensor papers must not drive LLM FT / LoRA–QLoRA answers.

    Returns True when the query is LLM-FT scoped and the evidence is dominated
    by HAR/sensor content without on-topic LLM FT signal.
    """
    if not is_llm_ft_query(query):
        return False
    blob = _evidence_blob(ev if isinstance(ev, dict) else {})
    if len(blob) < 20:
        return False
    if not is_har_sensor_text(blob):
        return False
    # Keep if the same source also clearly discusses LoRA/QLoRA/LLM FT.
    if LLM_FT_QUERY_RE.search(blob) and re.search(
        r"\b(?:VRAM|NF4|7B|70B|peak\s+mem|quantizat)\b", blob, re.I
    ):
        return False
    return True
