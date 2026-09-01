"""Heuristic RACE proxy scorer — aligns with DeepResearch Bench dimensions without external judge."""

from __future__ import annotations

import re

from app.domain.decompose import derive_slots, must_cover_from_slots
from app.domain.research_intent import user_goal
from app.report.deep_write import word_count
from app.report.race_write import memo_looks_truncated, missing_sections


def score_race_proxy(query: str, body_markdown: str, *, critic: dict | None = None) -> dict:
    text = body_markdown or ""
    goal = user_goal(query) or query or ""
    words = word_count(text)
    slots = derive_slots(goal, use_llm=False)
    must = must_cover_from_slots(slots)

    comp_hits = sum(1 for m in must if _mentions(text, m))
    comp = min(100, int(35 + 65 * comp_hits / max(1, len(must))))

    insight = 40
    if re.search(r"^##\s+Contradictions", text, re.I | re.M):
        insight += 15
    if re.search(r"^##\s+Quantitative findings", text, re.I | re.M) and "|" in text:
        insight += 15
    if text.count("### ") >= 3:
        insight += 15
    if re.search(r"versus|compared to|in contrast", text, re.I):
        insight += 10
    insight = min(100, insight)

    inst = 35
    anchors = [a for a in re.findall(r"[a-z0-9][a-z0-9-]{2,}", goal.lower()) if len(a) > 3][:6]
    inst += min(40, 8 * sum(1 for a in anchors if a in text.lower()))
    if re.search(r"^##\s+Decision rule", text, re.I | re.M):
        inst += 15
    inst = min(100, inst)

    read = 50
    if not memo_looks_truncated(text):
        read += 20
    if len(missing_sections(text)) == 0:
        read += 15
    if not re.search(r"\[(?:DIRECT|INFERRED)\]", text):
        read += 10
    read = min(100, read)

    depth = int(((critic or {}).get("depth_score") or {}).get("score") or 0)
    if depth:
        comp = int(round(0.6 * comp + 0.4 * depth))

    overall = int(round(0.30 * comp + 0.30 * insight + 0.25 * inst + 0.15 * read))
    return {
        "overall_proxy": overall,
        "comprehensiveness": comp,
        "insight": insight,
        "instruction_following": inst,
        "readability": read,
        "words": words,
        "missing_sections": missing_sections(text),
    }


def _mentions(text: str, phrase: str) -> bool:
    parts = [p for p in re.split(r"\W+", (phrase or "").lower()) if len(p) > 3]
    blob = (text or "").lower()
    return bool(parts) and sum(1 for p in parts if p in blob) >= max(1, len(parts) // 2)
