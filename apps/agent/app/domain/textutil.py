from __future__ import annotations

import re
from collections.abc import Iterable

GOAL_META_RE = re.compile(
    r"^(sector|geography|horizon|decision|must cover|constraints)\s*:",
    re.I,
)

# "/" is deliberately excluded so "Punica/LoRAX" yields two names.
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*")

STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those there here of in on at to for from by with
    without into over under about across after before between during through against per via as is are
    was were be been being am do does did doing done have has had having will would shall should can
    could may might must not no nor so such only own same too very just also more most other others
    some any each few many much both all every either neither one two three how what when where which
    who whom whose why whether does it its it's they them their we our you your i me my he she his her
    use used using uses make makes made get gets got give gives given need needs needed want wants
    explain explains analyze analyses analyzes describe describes compare compares tell show shows
    help helps let lets please kindly know knows think thinks like likes work works working
    thing things way ways case cases point points part parts kind sort type types lot lots
    good better best bad worse worst new old current modern different various several
    system systems approach approaches method methods technique techniques solution solutions
    question questions answer answers example examples detail details overview summary
    """.split()
)

GENERIC_ACRONYMS = frozenset(
    {
        "AI",
        "ML",
        "LLM",
        "LLMS",
        "GPU",
        "GPUS",
        "CPU",
        "CPUS",
        "TPU",
        "API",
        "APIS",
        "SDK",
        "OS",
        "IO",
        "HTTP",
        "HTTPS",
        "URL",
        "URLS",
        "JSON",
        "YAML",
        "CSV",
        "PDF",
        "UI",
        "UX",
        "SQL",
        "CLI",
        "FAQ",
        "PR",
        "OK",
    }
)


def user_goal(query: str) -> str:
    """Recover the original research goal from a briefing-composed query blob."""
    lines = [ln.strip() for ln in (query or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    goal_lines: list[str] = []
    for ln in lines:
        if GOAL_META_RE.match(ln):
            break
        goal_lines.append(ln)
    return " ".join(goal_lines).strip() or lines[0]


def tokens(text: str) -> list[str]:
    return [m.group(0) for m in WORD_RE.finditer(text or "")]


def normalized_tokens(text: str) -> list[str]:
    return [t.lower() for t in tokens(text)]


def content_terms(text: str, limit: int = 24) -> list[str]:
    """Topic-bearing terms with stopwords and pure filler removed."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tokens(text):
        term = raw.lower().strip("._/-")
        if len(term) < 3 or term in STOPWORDS or term in seen:
            continue
        if term.isdigit():
            continue
        seen.add(term)
        out.append(term)
        if len(out) >= limit:
            break
    return out


def distinctive_terms(text: str, limit: int = 12) -> list[str]:
    """Terms most likely to identify the specific subject of a question."""
    scored: list[tuple[float, str]] = []
    for raw in tokens(text):
        term = raw.strip("._/-")
        low = term.lower()
        if len(low) < 3 or low in STOPWORDS:
            continue
        score = 0.0
        if any(c.isupper() for c in term[1:]):
            score += 3.0
        elif term[:1].isupper():
            score += 1.0
        if any(c.isdigit() for c in term):
            score += 1.5
        if "-" in term or "_" in term or "." in term:
            score += 1.5
        score += min(2.0, len(low) / 6.0)
        scored.append((score, low))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[str] = []
    for _, term in scored:
        if term not in out:
            out.append(term)
        if len(out) >= limit:
            break
    return out


def entity_candidates(text: str, limit: int = 12) -> list[str]:
    """Proper-noun / product-like names, independent of any domain list."""
    goal = user_goal(text) or text or ""
    sentence_starts = {
        (m.group(1) or "").lower()
        for m in re.finditer(r"(?:^|[.!?]\s+)([A-Za-z][A-Za-z0-9-]*)", goal)
    }
    out: list[str] = []
    seen: set[str] = set()
    for raw in tokens(goal):
        term = raw.strip("._/-")
        if len(term) < 2:
            continue
        low = term.lower()
        if low in STOPWORDS or term.upper() in GENERIC_ACRONYMS:
            continue
        has_inner_upper = any(c.isupper() for c in term[1:])
        is_acronym = term.isupper() and len(term) >= 2
        starts_upper = term[:1].isupper()
        if not (has_inner_upper or is_acronym or starts_upper):
            continue
        if starts_upper and not has_inner_upper and not is_acronym and low in sentence_starts:
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(term)
        if len(out) >= limit:
            break
    return out


def mentions(term: str, text: str) -> bool:
    if not term or not text:
        return False
    return re.search(entity_pattern(term), text, re.I) is not None


def entity_pattern(term: str) -> str:
    """Regex that tolerates hyphen/space/case variants of a name."""
    parts = [re.escape(p) for p in re.split(r"[-_\s]+", (term or "").strip()) if p]
    if not parts:
        return r"(?!x)x"
    body = r"[-_\s]?".join(parts)
    return rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"


def term_overlap(a: str, b: str) -> int:
    return len(set(content_terms(a, limit=40)) & set(content_terms(b, limit=200)))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def singular(term: str) -> str:
    """Crude de-pluralisation so "cell" and "cells" compare as the same term."""
    low = (term or "").lower()
    if len(low) > 4 and low.endswith("ies"):
        return low[:-3] + "y"
    if len(low) > 3 and low.endswith("s") and not low.endswith(("ss", "us", "is", "as", "os")):
        return low[:-1]
    return low


def query_fingerprint(query: str) -> set[str]:
    return {singular(t) for t in content_terms(user_goal(query) or query, limit=40)}


def first_sentence_about(text: str, term: str, limit: int = 220) -> str:
    """Smallest self-contained passage in `text` that mentions `term`."""
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob:
        return ""
    pattern = entity_pattern(term)
    for sentence in re.split(r"(?<=[.!?])\s+", blob):
        if re.search(pattern, sentence, re.I):
            trimmed = sentence.strip()
            return trimmed[:limit] + ("…" if len(trimmed) > limit else "")
    match = re.search(pattern, blob, re.I)
    if not match:
        return ""
    start = max(0, match.start() - limit // 2)
    window = blob[start : start + limit].strip()
    return window + ("…" if len(blob) > start + limit else "")
