"""Verify Quantitative findings table rows against collected evidence.

LLMs confabulate baseline/treatment/delta pairs when the memo schema demands
structured numbers but sources only provide a subset. This module rejects rows
whose load-bearing figures do not appear in the cited evidence blob.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.report_audit import _section

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_CITE_IN_ROW_RE = re.compile(r"\[(\d+)(?:\s+(?:peer|repo|docs|news|specialist|vendor|preprint|primary|industry))?\]", re.I)
_PCT_RE = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?%")
_MULT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*x\b", re.I)
_N_EQ_RE = re.compile(r"\bn\s*=\s*(\d+)\b", re.I)
# Bare fractional scores (F1, accuracy, precision, recall) like "0.878" carry
# no %, x, or n= suffix, so they were previously invisible to _row_numbers —
# never checked against the source at all. A real memo cited "0.878
# (Sequential baseline)" for a number that is genuinely in the source table,
# just under a different architecture/model cell (Reflexive x Llama3, not
# Sequential x Claude) — the check never even looked at it.
_DECIMAL_SCORE_RE = re.compile(r"\b0\.\d{2,4}\b")
_FAKE_METRIC_RE = re.compile(
    r"\b(unverified|not\s+reported|not\s+explicitly|condition\s+not\s+stated|n/?a)\b",
    re.I,
)


def _norm_num(raw: str) -> str:
    return (raw or "").replace(",", "").rstrip("%").strip()


def _number_variants(raw: str) -> set[str]:
    """Generate match variants (38.60 ↔ 38.6, with/without %)."""
    base = _norm_num(raw)
    if not base:
        return set()
    out = {base, base + "%"}
    if "." in base:
        trimmed = base.rstrip("0").rstrip(".")
        if trimmed and trimmed != base:
            out.add(trimmed)
            out.add(trimmed + "%")
    return out


def number_in_source(num: str, source: str) -> bool:
    """True when num (or a close variant) appears in source text."""
    if _number_in_source_strict(num, source):
        return True
    raw = (num or "").strip()
    if raw.endswith("%") or "%" in raw:
        return _number_in_source_metric_context(num, source)
    return False


def _number_in_source_strict(num: str, source: str) -> bool:
    blob = source or ""
    if not blob.strip():
        return False
    blob_norm = blob.replace(",", "")
    raw = (num or "").strip()
    if not raw:
        return False

    mult = _MULT_RE.fullmatch(raw) or _MULT_RE.search(raw)
    if mult:
        base = mult.group(1) if mult.lastindex else mult.group(0).rstrip("xX").strip()
        patterns = [
            rf"{re.escape(base)}\s*x",
            rf"{re.escape(base)}\s*×",
            rf"{re.escape(base)}-fold",
            rf"{re.escape(base)}\s*times",
        ]
        return any(re.search(p, blob_norm, re.I) for p in patterns)

    n_eq = _N_EQ_RE.fullmatch(raw) or _N_EQ_RE.search(raw)
    if n_eq:
        val = n_eq.group(1) if n_eq.lastindex else re.sub(r"[^0-9]", "", raw)
        return bool(re.search(rf"\bn\s*=\s*{re.escape(val)}\b", blob_norm, re.I))

    for variant in _number_variants(raw):
        if not variant:
            continue
        needle = variant.rstrip("%")
        if re.search(rf"(?<![\d.]){re.escape(needle)}(?:%|\b)", blob_norm):
            return True
    return False


_OUTCOME_CONTEXT_RE = re.compile(
    r"\b(accuracy|f1|auc|bleu|rouge|correlation|improvement|gain|reduction|"
    r"pass@\d+|win rate|error rate|precision|recall|benchmark|confidence interval)\b",
    re.I,
)


def _number_in_source_metric_context(num: str, source: str, window: int = 110) -> bool:
    """Relaxed grounding: % near an outcome metric in the same local window."""
    blob = source or ""
    if not blob.strip():
        return False
    for variant in _number_variants(num):
        needle = variant.lower().rstrip("%")
        idx = blob.lower().find(needle)
        if idx < 0:
            continue
        ctx = blob[max(0, idx - window) : min(len(blob), idx + len(variant) + window)]
        if _OUTCOME_CONTEXT_RE.search(ctx):
            return True
    return False


def _evidence_blob(ev: dict) -> str:
    from app.domain.adversarial import results_section_blob

    full = str(ev.get("full_text") or "")
    focused = results_section_blob(full) if full else ""
    return (
        focused
        or full
        or ev.get("quote")
        or ev.get("snippet")
        or ev.get("search_snippet")
        or ev.get("title")
        or ""
    )


_METHOD_PHRASE_RE = re.compile(r"\bMethod\s+([A-Z])\b", re.I)
_METHOD_NAME_RE = re.compile(r"\bMethod\s+([A-Za-z][\w-]+)\b", re.I)
_METHOD_TOKEN_RE = re.compile(r"\b([A-Z][A-Za-z0-9][\w-]{1,24})\b")
_METRIC_TOKEN_RE = re.compile(
    r"\b(accuracy|f1|auc|bleu|rouge|latency|throughput|pass@\d+|error rate|precision|recall)\b",
    re.I,
)


def _row_entity_tokens(cells: list[str]) -> list[str]:
    tokens: list[str] = []
    for cell in cells:
        if _PCT_RE.search(cell) or _MULT_RE.search(cell) or _N_EQ_RE.search(cell):
            continue
        if _CITE_IN_ROW_RE.search(cell):
            continue
        phrase = _METHOD_PHRASE_RE.search(cell)
        if phrase:
            tokens.append(f"Method {phrase.group(1).upper()}")
        name = _METHOD_NAME_RE.search(cell)
        if name:
            tokens.append(f"Method {name.group(1)}")
        for match in _METHOD_TOKEN_RE.finditer(cell):
            tok = match.group(1).strip()
            if tok.lower() not in {"the", "our", "this", "table", "figure", "section", "method"}:
                tokens.append(tok)
        for match in _METRIC_TOKEN_RE.finditer(cell):
            tokens.append(match.group(1))
        cleaned = re.sub(r"\[\d+[^\]]*\]", "", cell).strip()
        if cleaned and len(cleaned) <= 32 and not _PCT_RE.search(cleaned):
            tokens.append(cleaned)
    return list(dict.fromkeys(tokens))


def _number_local_context(source: str, num: str, window: int = 40) -> str:
    blob = source or ""
    for variant in _number_variants(num):
        needle = variant.lower().rstrip("%")
        start = 0
        while True:
            idx = blob.lower().find(needle, start)
            if idx < 0:
                break
            ctx_start = max(0, idx - window)
            ctx_end = min(len(blob), idx + len(variant) + window)
            return blob[ctx_start:ctx_end]
            start = idx + 1
    return ""


def _attributed_method_before(source: str, num: str) -> str:
    """Last 'Method X' label appearing before this number in the source."""
    blob = source or ""
    blob_low = blob.lower()
    for variant in _number_variants(num):
        needle = variant.lower().rstrip("%")
        idx = blob_low.find(needle)
        if idx < 0:
            continue
        prefix = blob[:idx]
        matches = list(_METHOD_PHRASE_RE.finditer(prefix))
        if matches:
            return f"Method {matches[-1].group(1).upper()}"
        matches = list(_METHOD_NAME_RE.finditer(prefix))
        if matches:
            return f"Method {matches[-1].group(1)}"
    return ""


_PAREN_LABEL_RE = re.compile(r"\(([^)]{2,50})\)")
_CELL_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?%?")


def _cell_number_labels(cell: str) -> list[tuple[str, str]]:
    """Pairs of (number, its own parenthetical qualifier) within one cell,
    e.g. "0.878 (Sequential baseline)" -> [("0.878", "Sequential baseline")].
    Scoped to one cell (not the whole row) so the qualifier is unambiguously
    this number's own claimed condition, not some other cell's label."""
    pairs: list[tuple[str, str]] = []
    for m in _CELL_NUM_RE.finditer(cell):
        num = m.group(0)
        if not (re.fullmatch(r"0\.\d{2,4}", num) or num.endswith("%")):
            continue
        tail = cell[m.end() : m.end() + 60].lstrip()
        label_m = _PAREN_LABEL_RE.match(tail)
        if label_m:
            pairs.append((num, label_m.group(1).strip()))
    return pairs


def semantic_number_grounded(row: dict[str, Any], source: str) -> tuple[bool, str]:
    """Catch a number that is genuinely IN the source but under a different
    named condition than this row claims — e.g. a real memo cited "0.878
    (Sequential baseline)" for a figure that the source's benchmark table
    lists under Reflexive x Llama3, not Sequential x Claude. number_in_source
    alone passes this (0.878 really is in the text), so a claim can be
    "grounded" by bare substring match and still misattributed to the wrong
    row/column of a multi-condition table.
    
    Now also checks: (1) key words from the entire row (Metric + Condition 
    columns) must appear near the number in source, and (2) antonym pairs 
    like failure/success must match.
    """
    cells = row.get("cells") or []
    numbers = row.get("numbers") or []
    entities = _row_entity_tokens(cells)
    method_entities = [e for e in entities if e.lower().startswith("method ")]
    if numbers and method_entities:
        for num in numbers:
            if not number_in_source(num, source):
                continue
            attributed = _attributed_method_before(source, num)
            if not attributed:
                continue
            if not any(ent.lower() == attributed.lower() for ent in method_entities):
                return False, f"{num} is attributed to {attributed} in source, not {method_entities[0]}."

    # Original per-cell check for numbers with parenthetical labels
    for cell in cells:
        for num, label in _cell_number_labels(cell):
            if not number_in_source(num, source):
                continue
            key_word = re.split(r"\s+", label)[0].strip(",.;:")
            if len(key_word) < 4:
                continue
            local = _number_local_context(source, num, window=250)
            if local and key_word.lower() not in local.lower():
                return False, f"{num} ({label}) not attributed to '{key_word}' near this figure in source."
    
    # NEW: Whole-row semantic check for naked numbers
    # Extract all key words from Metric + Baseline/Condition columns (≥4 chars)
    context_words = []
    for cell in cells:
        for word in re.findall(r"[A-Za-z+]{4,}", cell):
            context_words.append(word)
    
    # Antonym pairs that should NOT co-occur (row vs source)
    ANTONYM_PAIRS = [
        (r"\b(failure|error|failed|errors)\b", r"\b(success|yield|passed|successful)\b"),
        (r"\b(success|yield|passed|successful)\b", r"\b(failure|error|failed|errors)\b"),
    ]
    
    for num in numbers:
        if not number_in_source(num, source):
            continue
        
        local = _number_local_context(source, num, window=250)
        if not local:
            continue
        
        # Check 1: Row keywords should appear in source context
        local_lower = local.lower()
        missing = [w for w in context_words if w.lower() not in local_lower]
        # Allow some flexibility: if >50% of key words are missing, flag it
        if len(missing) > len(context_words) * 0.5 and len(context_words) >= 2:
            return False, f"{num}: key words {missing[:3]} from row not found near number in source."
        
        # Check 2: Antonym detection (row says failure, source says success)
        row_text = " | ".join(cells).lower()
        for row_pattern, source_pattern in ANTONYM_PAIRS:
            if re.search(row_pattern, row_text, re.I) and re.search(source_pattern, local, re.I):
                row_term = re.search(row_pattern, row_text, re.I).group(0)
                src_term = re.search(source_pattern, local, re.I).group(0)
                return False, f"{num}: row mentions '{row_term}' but source context has '{src_term}' (semantic mismatch)."
    
    return True, ""


def _row_numbers(row_text: str) -> list[str]:
    """Load-bearing figures in a table row (% / multipliers / n= counts)."""
    found: list[str] = []
    seen: set[str] = set()
    text = row_text or ""

    def _add(token: str, key: str) -> None:
        if key in seen:
            return
        seen.add(key)
        found.append(token)

    for match in _PCT_RE.finditer(text):
        token = match.group(0)
        _add(token, _norm_num(token))
    for match in _MULT_RE.finditer(text):
        token = match.group(0)
        _add(token, token.lower().replace(" ", ""))
    for match in _N_EQ_RE.finditer(text):
        token = match.group(0)
        _add(token, token.lower().replace(" ", ""))
    for match in _DECIMAL_SCORE_RE.finditer(text):
        token = match.group(0)
        _add(token, _norm_num(token))
    return found


def _row_is_filler(row: dict[str, Any]) -> bool:
    blob = " | ".join(row.get("cells") or [])
    if _FAKE_METRIC_RE.search(blob):
        return True
    nums = row.get("numbers") or []
    if not nums:
        return True
    return False


def parse_quantitative_table_rows(section: str) -> list[dict[str, Any]]:
    """Parse markdown table rows under Quantitative findings."""
    lines = [
        ln
        for ln in (section or "").splitlines()
        if _TABLE_ROW_RE.match(ln.strip())
    ]
    if not lines:
        return []

    rows: list[dict[str, Any]] = []
    data_start = 0
    if len(lines) >= 2 and all(
        set(c.strip()) <= {"-", " "}
        for c in lines[1].strip().strip("|").split("|")
    ):
        data_start = 2
    elif len(lines) >= 1:
        # Headerless single-row or header+rows without separator
        first_cells = [c.strip() for c in lines[0].strip().strip("|").split("|")]
        headerish = any(
            h in " ".join(first_cells).lower()
            for h in ("baseline", "metric", "domain", "source", "value", "treatment")
        )
        data_start = 1 if headerish else 0

    for line in lines[data_start:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(set(c) <= {"-", " "} for c in cells):
            continue
        cite_match = None
        for cell in reversed(cells):
            cite_match = _CITE_IN_ROW_RE.search(cell)
            if cite_match:
                break
        rows.append(
            {
                "line": line,
                "cells": cells,
                "cite_n": int(cite_match.group(1)) if cite_match else None,
                "numbers": _row_numbers(line),
            }
        )
    return rows


def verify_quantitative_row(
    row: dict[str, Any],
    *,
    citations: list[dict],
    evidence: list[dict],
) -> dict[str, Any]:
    """Check each figure in a table row against the cited source evidence."""
    by_n = {int(c["n"]): c for c in citations if c.get("n") is not None}
    by_url = {
        (ev.get("url") or "").strip().rstrip("/").lower(): ev for ev in evidence if ev.get("url")
    }
    numbers = row.get("numbers") or []
    cite_n = row.get("cite_n")
    if not numbers:
        return {"status": "no_numbers", "missing": [], "cite_n": cite_n}
    if cite_n is None:
        return {
            "status": "no_cite",
            "missing": numbers,
            "note": "Row has figures but no [n] citation — cannot ground.",
        }
    cite = by_n.get(cite_n) or {}
    url = (cite.get("url") or "").strip().rstrip("/").lower()
    ev = by_url.get(url) or {}
    source = _evidence_blob(ev) or _evidence_blob(cite)
    if len(source.strip()) < 25:
        return {
            "status": "source_missing",
            "missing": numbers,
            "cite_n": cite_n,
            "url": url,
            "note": f"[{cite_n}] source text too short to verify figures.",
        }
    missing = [n for n in numbers if not number_in_source(n, source)]
    if missing:
        return {
            "status": "wrong_number",
            "missing": missing,
            "cite_n": cite_n,
            "url": url,
            "note": f"Figures not found in [{cite_n}] source: {', '.join(missing[:6])}.",
        }
    ok, sem_note = semantic_number_grounded(row, source)
    if not ok:
        return {
            "status": "semantic_mismatch",
            "missing": numbers,
            "cite_n": cite_n,
            "url": url,
            "note": sem_note,
        }
    return {"status": "verified", "missing": [], "cite_n": cite_n, "url": url}


def audit_quantitative_table(
    markdown: str,
    *,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> list[str]:
    """Return human-readable integrity notes for ungrounded quantitative rows."""
    section = _section(markdown or "", "Quantitative findings")
    if not section or "|" not in section:
        return []
    cites = citations or []
    evs = evidence or []
    issues: list[str] = []
    for row in parse_quantitative_table_rows(section):
        result = verify_quantitative_row(row, citations=cites, evidence=evs)
        status = result.get("status")
        if status == "verified":
            continue
        if status == "no_numbers" and not _row_is_filler(row):
            continue
        label = " | ".join(row.get("cells") or [])[:90]
        note = result.get("note") or status
        issues.append(f"Ungrounded quantitative row ({note}): {label}")
    return issues


_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _is_separator_row(line: str) -> bool:
    cells = [c.strip() for c in (line or "").strip().strip("|").split("|")]
    return bool(cells) and all(bool(_SEPARATOR_CELL_RE.match(c)) for c in cells)


def _first_row_cell_count(line: str) -> int:
    """Cells in the first logical row of `line`, stopping at the first empty
    ('| |') boundary token if the writer joined more rows onto the same line."""
    raw = (line or "").strip().strip("|")
    count = 0
    for cell in raw.split("|"):
        if not cell.strip():
            break
        count += 1
    return count


def repair_quantitative_table(markdown: str) -> str:
    """Rebuild the Quantitative findings table when the writer emits rows
    joined onto one line ("| a | b || c | d |") or — worse — flattens the
    entire table (header, every row, no separator) onto a single line with
    "| |" between logical rows. Either shape renders as a wall of text with
    literal '|' characters instead of a table, since GFM needs one row per
    line plus a header separator row.

    Column count comes from the first logical row (stopping at the first
    empty '| |' boundary token, if any) so a table that's merely missing its
    separator row but otherwise fine is left untouched.
    """
    section = _section(markdown or "", "Quantitative findings")
    if not section:
        return markdown

    lines = section.splitlines()
    table_lines = [ln for ln in lines if ln.strip().startswith("|")]
    if not table_lines:
        return markdown

    col_count = _first_row_cell_count(table_lines[0])
    if col_count < 2:
        return markdown

    all_cells: list[str] = []
    for ln in table_lines:
        if _is_separator_row(ln):
            continue
        for cell in ln.strip().strip("|").split("|"):
            cell = cell.strip()
            if cell:
                all_cells.append(cell)

    if not all_cells or len(all_cells) % col_count != 0 or len(all_cells) <= col_count:
        # Doesn't cleanly regroup (or it's just a header) — leave as-is
        # rather than guess wrong and corrupt a table that was already fine.
        return markdown

    rows = [all_cells[i : i + col_count] for i in range(0, len(all_cells), col_count)]
    header, *data_rows = rows
    rebuilt = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * col_count,
        *("| " + " | ".join(row) + " |" for row in data_rows),
    ]

    if rebuilt == table_lines:
        return markdown

    out_lines: list[str] = []
    inserted = False
    for ln in lines:
        if ln.strip().startswith("|"):
            if not inserted:
                out_lines.extend(rebuilt)
                inserted = True
            continue
        out_lines.append(ln)
    return _replace_section(markdown, "Quantitative findings", "\n".join(out_lines))


def sanitize_quantitative_table(
    markdown: str,
    *,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Drop table rows whose figures are not traceable to cited evidence."""
    markdown = repair_quantitative_table(markdown)
    section = _section(markdown or "", "Quantitative findings")
    if not section or "|" not in section:
        return markdown, {"removed": 0, "kept": 0, "issues": []}

    cites = citations or []
    evs = evidence or []
    parsed = parse_quantitative_table_rows(section)
    if not parsed:
        return markdown, {"removed": 0, "kept": 0, "issues": []}

    kept_lines: list[str] = []
    removed: list[dict] = []
    issues: list[str] = []
    for row in parsed:
        if _row_is_filler(row):
            removed.append({**row, "result": {"status": "filler_row"}})
            label = " | ".join(row.get("cells") or [])[:90]
            issues.append(f"Removed unverifiable quantitative row (no grounded figures): {label}")
            continue
        result = verify_quantitative_row(row, citations=cites, evidence=evs)
        if result.get("status") == "verified":
            kept_lines.append(row["line"])
        else:
            removed.append({**row, "result": result})
            note = result.get("note") or result.get("status")
            label = " | ".join(row.get("cells") or [])[:90]
            issues.append(f"Removed ungrounded row ({note}): {label}")

    if not removed:
        return markdown, {"removed": 0, "kept": len(kept_lines), "issues": []}

    # Rebuild section: keep non-table prose + header + kept rows only.
    lines = section.splitlines()
    out_lines: list[str] = []
    table_mode = False
    header_done = False
    for line in lines:
        if not _TABLE_ROW_RE.match(line.strip()):
            out_lines.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not header_done:
            out_lines.append(line)
            if cells and not all(set(c) <= {"-", " "} for c in cells):
                header_done = True
            table_mode = True
            continue
        if all(set(c) <= {"-", " "} for c in cells):
            out_lines.append(line)
            continue
        if line in kept_lines:
            out_lines.append(line)

    if kept_lines:
        new_section = "\n".join(out_lines).strip()
    else:
        prose = [ln for ln in out_lines if not ln.strip().startswith("|")]
        new_section = "\n".join(prose).strip()
        new_section += (
            "\n\nNo numeric results could be verified against collected source excerpts. "
            "Figures that appeared in an earlier draft were removed because they were not "
            "traceable to the cited evidence (likely model confabulation under a fixed table schema)."
        )

    new_body = _replace_section(markdown, "Quantitative findings", new_section)
    return new_body, {
        "removed": len(removed),
        "kept": len(kept_lines),
        "issues": issues,
    }


def quant_row_verified(
    row: dict[str, Any],
    *,
    citations: list[dict],
    evidence: list[dict],
) -> bool:
    """True when an extracted quant row's figure appears in the cited source full text."""
    n = row.get("n")
    if n in (None, "", "?"):
        return False
    try:
        cite_n = int(n)
    except (TypeError, ValueError):
        return False
    by_n = {int(c["n"]): c for c in citations if c.get("n") is not None}
    by_url = {
        (ev.get("url") or "").strip().rstrip("/").lower(): ev for ev in evidence if ev.get("url")
    }
    cite = by_n.get(cite_n) or {}
    url = (cite.get("url") or row.get("url") or "").strip().rstrip("/").lower()
    ev = by_url.get(url) or {}
    source = _evidence_blob(ev) or _evidence_blob(cite)
    if len(source.strip()) < 25:
        return False
    metric = str(row.get("metric") or row.get("value") or row.get("metric_name") or "").strip()
    if not metric:
        return False
    if not number_in_source(metric, source):
        return False
    ok, _ = semantic_number_grounded({"numbers": [metric], "cells": [metric]}, source)
    return ok


def filter_verified_quant_rows(
    rows: list[dict[str, Any]],
    *,
    citations: list[dict],
    evidence: list[dict],
) -> list[dict[str, Any]]:
    """Keep only quant rows grounded in cited source excerpts."""
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if quant_row_verified(row, citations=citations, evidence=evidence):
            row = dict(row)
            row["warning"] = row.get("warning") or "Verified in excerpt"
            out.append(row)
    return out


def _replace_section(markdown: str, heading: str, new_body: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.I | re.M)
    match = pattern.search(markdown)
    if not match:
        return markdown
    start = match.end()
    rest = markdown[start:]
    nxt = re.search(r"^##\s+", rest, re.M)
    end = start + (nxt.start() if nxt else len(rest))
    body = f"\n\n{new_body.strip()}\n\n" if new_body.strip() else "\n\n"
    return markdown[: match.start()] + f"## {heading}" + body + (rest[nxt.start() :] if nxt else "")


def format_allowed_quant_block(
    evidence: list[dict],
    citations: list[dict] | None = None,
) -> str:
    """Writer constraint: only these figures may appear in Quantitative findings."""
    from app.domain.adversarial import extract_quantitative_rows, quantitative_table_markdown
    from app.domain.quantitative_verify import filter_verified_quant_rows

    rows = filter_verified_quant_rows(
        extract_quantitative_rows(evidence, citations),
        citations=citations or [],
        evidence=evidence or [],
    )
    if not rows:
        return (
            "QUANTITATIVE TABLE CONSTRAINT:\n"
            "- No verified outcome metrics were extracted from collected excerpts.\n"
            "- Omit ## Quantitative findings entirely OR write one sentence: measurements not found.\n"
            "- FORBIDDEN: inventing baseline/treatment/delta/% rows to fill the table schema."
        )
    return (
        "QUANTITATIVE TABLE CONSTRAINT (mandatory — copy ONLY from this list):\n"
        "- Each table row must use one metric below verbatim; do NOT synthesize baseline→treatment pairs.\n"
        "- If you cannot pair two numbers as before/after from the SAME source sentence, "
        "use single-value rows (Metric | Value | Condition | Source [n]).\n"
        "- FORBIDDEN: padding empty cells with plausible percentages.\n\n"
        + quantitative_table_markdown(rows)
    )
