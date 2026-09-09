"""Filter evidence before memo artifacts (matrix, contrast, quant extraction)."""

from __future__ import annotations

import re

from app.domain.coverage import _anchor_hits, _anchors, _blob, tag_evidence_roles
from app.domain.research_intent import user_goal

_MEMO_NOISE_RE = re.compile(
    r"\b("
    r"custom\s+dropout\s+layer|dropout\s+rate|dropout\s+layer|"
    r"weight\s+decay|batch\s+normalization|learning\s+rate\s+of|"
    r"hidden\s+layer|fully\s+connected|convolutional\s+layer|"
    r"randomly\s+setting\s+\d+%\s+of\s+input\s+units|"
    r"figure\s+\d+|table\s+\d+|appendix\s+[a-z]"
    r")\b",
    re.I,
)

_INTRUSION_OFF_TOPIC_RE = re.compile(
    r"\b(intrusion\s+detection|federated\s+learning|iot\s+network|"
    r"network\s+intrusion|ddos|malware\s+classification)\b",
    re.I,
)


def is_memo_excerpt_noise(text: str) -> bool:
    """HTML/PDF fragments that look numeric but are not outcome claims."""
    clean = (text or "").strip()
    if not clean:
        return True
    if _MEMO_NOISE_RE.search(clean):
        return True
    if re.search(r"\bincorporates\s+a\s+custom\b", clean, re.I):
        return True
    return False


def filter_memo_evidence(query: str, evidence: list[dict], *, min_anchor_hits: int = 1) -> list[dict]:
    """Drop off-topic / low-relevance sources before matrix, contrast, and quant."""
    from app.graph.nodes.scholar import _on_topic

    goal = user_goal(query) or query or ""
    tagged = tag_evidence_roles(evidence or [], goal)
    anchors = _anchors(goal)
    kept: list[dict] = []
    for ev in tagged:
        if ev.get("off_topic"):
            continue
        title = str(ev.get("title") or "")
        blob = _blob(ev)
        if _INTRUSION_OFF_TOPIC_RE.search(f"{title} {blob[:1200]}"):
            if not _INTRUSION_OFF_TOPIC_RE.search(goal):
                continue
        hits = _anchor_hits(blob, anchors) if anchors else 1
        url = (ev.get("url") or "").lower()
        is_paper = "arxiv" in url or "aclanthology" in url or ev.get("source_agent") == "scholar"
        topical = _on_topic(title, blob[:2400], goal) or (is_paper and hits >= 1) or hits >= 2
        if not topical:
            continue
        if not is_paper and anchors and hits < min_anchor_hits:
            continue
        kept.append(ev)
    if kept:
        return kept
    # If everything was filtered, keep tagged non-off-topic rather than empty matrix.
    return [e for e in tagged if not e.get("off_topic")] or list(evidence or [])
