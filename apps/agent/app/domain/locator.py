"""Deterministic quote locators: heading, paragraph, or PDF page marker.

No LLM. Locator labels are only emitted when the quote (or a high-overlap
span) is actually found in the retrieved text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.citations import normalize, quote_in_source

PAGE_MARK_RE = re.compile(r"\[\[page\s+(\d+)\]\]", re.I)
HEADING_RE = re.compile(
    r"^(?:#{1,4}\s+|(?:\d+\.){1,3}\s+)(.{8,120})$|^([A-Z][A-Za-z0-9 ,/\-]{8,80})$",
    re.M,
)
TABLE_RE = re.compile(r"\b(table|tab\.)\s+(\d+[a-z]?)\b", re.I)
SECTION_RE = re.compile(r"\b(?:section|sec\.)\s+(\d+(?:\.\d+)*)\b", re.I)


@dataclass
class Locator:
    kind: str = "paragraph"  # page | heading | table | paragraph
    label: str = ""
    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    found: bool = False

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "page": self.page,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "found": self.found,
        }


@dataclass
class StructuredText:
    text: str
    headings: list[tuple[str, int]] = field(default_factory=list)
    pages: list[tuple[int, int]] = field(default_factory=list)  # page, char offset


def parse_structured(text: str, headings: list[tuple[str, int]] | None = None) -> StructuredText:
    raw = text or ""
    pages: list[tuple[int, int]] = []
    for match in PAGE_MARK_RE.finditer(raw):
        pages.append((int(match.group(1)), match.start()))
    found_headings = list(headings or [])
    if not found_headings:
        for match in HEADING_RE.finditer(raw):
            title = (match.group(1) or match.group(2) or "").strip()
            if title and len(title.split()) <= 14:
                found_headings.append((title, match.start()))
    return StructuredText(text=raw, headings=found_headings, pages=pages)


def _find_span(quote: str, source: str) -> tuple[int, int] | None:
    q = (quote or "").strip()
    s = source or ""
    if len(q) < 8 or not s:
        return None
    idx = s.lower().find(q.lower())
    if idx >= 0:
        return idx, idx + len(q)
    nq = normalize(q)
    ns = normalize(s)
    idx = ns.find(nq) if len(nq) >= 12 else -1
    if idx >= 0:
        return idx, idx + len(nq)
    words = [w for w in nq.split() if len(w) > 2][:12]
    if len(words) < 4:
        return None
    window = " ".join(words[:8])
    idx = ns.find(window)
    if idx >= 0:
        return idx, idx + len(window)
    return None


def locate_quote(
    quote: str,
    source: str,
    *,
    headings: list[tuple[str, int]] | None = None,
) -> Locator:
    structured = parse_structured(source, headings)
    text = structured.text
    if not quote_in_source(quote, text):
        return Locator(found=False)
    span = _find_span(quote, text)
    start, end = span or (None, None)
    page = None
    if start is not None and structured.pages:
        for num, offset in structured.pages:
            if offset <= start:
                page = num
            else:
                break
    heading = ""
    if start is not None and structured.headings:
        for title, offset in structured.headings:
            if offset <= start:
                heading = title
            else:
                break
    table = TABLE_RE.search(quote or "") or (
        TABLE_RE.search(text[max(0, (start or 0) - 80) : (end or 0) + 80]) if start is not None else None
    )
    section = SECTION_RE.search(heading) or SECTION_RE.search(quote or "")
    if table:
        kind, label = "table", f"Table {table.group(2)}"
    elif page is not None:
        kind, label = "page", f"p.{page}" + (f" · {heading}" if heading else "")
    elif heading:
        kind, label = "heading", heading[:80]
    elif section:
        kind, label = "heading", f"Section {section.group(1)}"
    else:
        kind, label = "paragraph", "passage"
    return Locator(kind=kind, label=label, page=page, char_start=start, char_end=end, found=True)
