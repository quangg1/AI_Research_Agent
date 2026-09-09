"""Deterministic memo consolidation: dedupe sections, merge gaps, guard thresholds."""

from __future__ import annotations

import re

from app.domain.report_audit import _section

_CITE_RE = re.compile(r"\[\d+(?:\s+(?:peer|repo|docs|news|preprint|primary|specialist|vendor|unreliable|industry))?\]")
_NESTED_CONTRA_RE = re.compile(
    r"^#{3,4}\s+.*(?:contradict|counter-evidence|counter evidence|open questions?)\s*$",
    re.I | re.M,
)
_METRIC_GAPS_HEADING_RE = re.compile(r"^#{3}\s+Metric gaps\s*$", re.I | re.M)
_THRESHOLD_LINE_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*%\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*%|"
    r"(?:≥|<=|>=|<|≤|above|below|under|over|exceeds?|at least|limit)\s*\d+(?:\.\d+)?\s*%?|"
    r"\d+(?:\.\d+)?\s*%\s+(?:synthetic|threshold|cutoff|saturation|limit))",
    re.I,
)
_HEURISTIC_MARKERS = re.compile(
    r"heuristic|re-benchmark|rebenchmark|not a universal|illustrative|engineering heuristic|"
    r"based on\s*\[\d+|source\s*\[\d+|evidence-backed threshold:\s*none",
    re.I,
)
# A writer sometimes promotes its own transition sentence into a subheading,
# e.g. "### Then we address alignment mechanisms..." — that reads as a
# leftover instruction, not a section title.
_LEAKED_TRANSITION_HEADING_RE = re.compile(
    r"^(#{3,4})\s+((?:then|and|so|thus|next|now|furthermore|moreover|therefore|here)\b.*)$",
    re.I,
)
# writer_system() unconditionally bans "ASCII art diagrams" and "fenced code
# blocks" in the memo body, but the model doesn't reliably obey — seen twice
# in real output: once as a content-free block of arrows/whitespace, once as
# an elaborate (and misaligned) box diagram whose content duplicated prose
# already given nearby. Since the rule has no exceptions, strip every fenced
# block rather than trying to judge which ones are "informative enough".
_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```\n?")


def _strip_fenced_code_blocks(text: str) -> str:
    return _FENCED_BLOCK_RE.sub("", text)


def consolidate_memo_structure(
    markdown: str,
    *,
    citations: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> str:
    """Post-write structural pass: one home per topic, no duplicate gap lists."""
    text = (markdown or "").strip()
    if not text:
        return text
    text = _strip_nested_contradictions(text)
    text = _fix_leaked_transition_headings(text)
    text = _strip_fenced_code_blocks(text)
    text = _merge_metric_gaps_into_uncertainties(text)
    text = _polish_source_quality_section(text, citations)
    text = _sanitize_quantitative_table(text)
    if citations is not None or evidence is not None:
        text = _drop_ungrounded_quantitative_rows(text, citations=citations or [], evidence=evidence or [])
    text = _sanitize_decision_thresholds(text)
    return re.sub(r"\n{4,}", "\n\n\n", text).strip() + "\n"


def _drop_ungrounded_quantitative_rows(
    text: str, *, citations: list[dict], evidence: list[dict]
) -> str:
    """Second, stricter pass: drop rows whose figures do not verbatim-match the
    cited source's excerpt (catches confabulated baseline/treatment pairs that
    the lightweight filler-row filter above lets through because they DO have
    a number, just not one the source actually reported)."""
    from app.domain.quantitative_verify import sanitize_quantitative_table

    new_text, _report = sanitize_quantitative_table(text, citations=citations, evidence=evidence)
    return new_text


def _fix_leaked_transition_headings(text: str) -> str:
    """Demote a heading that is really a leaked transition sentence to a
    bold lead-in line under the heading before it, keeping the content but
    dropping the fake structure (no lost prose, no phantom subsection)."""
    changed = False
    out: list[str] = []
    for line in text.splitlines():
        match = _LEAKED_TRANSITION_HEADING_RE.match(line.strip())
        sentence = match.group(2).strip() if match else ""
        if match and len(sentence.split()) > 3:
            if not sentence.endswith((".", "?", "!")):
                sentence += "."
            out.append(f"**{sentence}**")
            changed = True
            continue
        out.append(line)
    return "\n".join(out) if changed else text


def _strip_nested_contradictions(text: str) -> str:
    if not _section(text, "Contradictions & debates"):
        return text
    analysis = _section(text, "Detailed analysis")
    if not analysis or not _NESTED_CONTRA_RE.search(analysis):
        return text
    cleaned = _remove_matching_subsections(analysis, _NESTED_CONTRA_RE)
    if cleaned == analysis:
        return text
    return _replace_section(text, "Detailed analysis", cleaned)


def _merge_metric_gaps_into_uncertainties(text: str) -> str:
    quant = _section(text, "Quantitative findings")
    if not quant or not _METRIC_GAPS_HEADING_RE.search(quant):
        return text
    gaps_body = _extract_subsection(quant, _METRIC_GAPS_HEADING_RE)
    if not gaps_body.strip():
        quant_clean = _remove_subsection(quant, _METRIC_GAPS_HEADING_RE)
        text = _replace_section(text, "Quantitative findings", quant_clean)
        return text

    bullets = _bullet_lines(gaps_body)
    if not bullets:
        quant_clean = _remove_subsection(quant, _METRIC_GAPS_HEADING_RE)
        return _replace_section(text, "Quantitative findings", quant_clean)

    unc = _section(text, "Uncertainties & gaps")
    merged_block = (
        "### Measurement gaps (from sources)\n"
        + "\n".join(bullets)
        + "\n"
    )
    if unc:
        if "measurement gaps" in unc.lower():
            new_unc = unc
        else:
            new_unc = f"{unc.rstrip()}\n\n{merged_block}".strip()
    else:
        new_unc = merged_block.strip()

    text = _replace_section(text, "Quantitative findings", _remove_subsection(quant, _METRIC_GAPS_HEADING_RE))
    if _section(text, "Uncertainties & gaps"):
        text = _replace_section(text, "Uncertainties & gaps", new_unc)
    else:
        text = _insert_before_section(text, "Limitations", f"## Uncertainties & gaps\n\n{new_unc}\n")
    return text


_CITE_PARSE = re.compile(r"\[(\d+)(?:\s+(peer|repo|docs|news|preprint|primary|specialist|vendor|unreliable|industry))?\]")
_QUALITATIVE_METRIC_RE = re.compile(
    r"\b(eliminates?|prevents?|reduces?|improves?|enables?|addresses?|"
    r"hallucination|collapse|without|not reported|qualitative|mechanism)\b",
    re.I,
)
_HAS_MEASURED_NUMBER_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|ms|µs|us|s|sec|×|x\b|tok/s|tokens?/s|gb/s|gflop|tflop|"
    r"req/s|minutes?|min\b)|\b\d{1,3}(?:,\d{3})+\b",
    re.I,
)


def merge_inline_citations(text: str) -> str:
    """Merge 3+ adjacent [n peer] tags into one group: [1, 6, 8, 9 peer]."""
    raw = text or ""
    cites = _CITE_PARSE.findall(raw)
    if len(cites) <= 2:
        return raw
    nums = sorted({int(n) for n, _ in cites})
    suffixes = [s for _, s in cites if s]
    suffix = ""
    if suffixes and len(set(suffixes)) == 1:
        suffix = f" {suffixes[0]}"
    merged = f"[{', '.join(str(n) for n in nums)}{suffix}]"
    prose = _CITE_RE.sub("", raw)
    prose = re.sub(r"\s+", " ", prose).strip().rstrip(".")
    if not prose:
        return merged
    return f"{prose} {merged}"


def _polish_source_quality_section(text: str, citations: list[dict] | None = None) -> str:
    section = _section(text, "Source quality")
    used_citations = citations
    if citations:
        from app.domain.citations import _cited_numbers

        cited = _cited_numbers(text)
        if cited:
            # Same reasoning as References: a ranked-but-never-cited source
            # shouldn't get a tier-band entry either, or the two lists
            # disagree about how many sources the memo actually draws on.
            used_citations = [c for c in citations if (c.get("n") if isinstance(c, dict) else None) in cited]
    if not section:
        # The writer sometimes skips this heading outright rather than
        # leaving it half-filled (real memo output: no "## Source quality"
        # anywhere in the body at all). deep_write.py's prompt requires it —
        # insert one built from the ledger rather than publish without it.
        if used_citations:
            from app.domain.citations import format_source_quality_section

            rebuilt = format_source_quality_section(used_citations)
            if rebuilt:
                return _insert_before_section(text, "References", f"## Source quality\n\n{rebuilt}")
        return text
    if used_citations:
        # The writer LLM is unreliable here — real memos have left a band's
        # citation list empty ("Band B — Specialist ...: ") despite that
        # tier's sources being cited throughout the body. Rebuild from the
        # ledger instead of trying to repair LLM prose, same as References.
        from app.domain.citations import format_source_quality_section

        rebuilt = format_source_quality_section(used_citations)
        if rebuilt:
            return _replace_section(text, "Source quality", rebuilt)
    polished = _merge_source_quality_bullets(section)
    if polished.strip() == section.strip():
        return text
    return _replace_section(text, "Source quality", polished)


def _source_quality_label_key(line: str) -> str:
    """Normalize to band/title prefix so 'Includes X' vs 'Includes Y' still merge."""
    stripped = line.lstrip("-*").strip()
    prose = _CITE_RE.sub("", stripped).strip().rstrip(".")
    head = prose.split(":", 1)[0] if ":" in prose else prose
    head = re.split(r"\bincludes\b", head, maxsplit=1, flags=re.I)[0]
    return re.sub(r"\s+", " ", head).strip().lower()


def _merge_source_quality_bullets(section: str) -> str:
    """One bullet per band label; merge all cites into [1, 6, 8, 9 peer]."""
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    other_lines: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith(("-", "*")):
            other_lines.append(line)
            continue
        label = _source_quality_label_key(stripped)
        if not label:
            other_lines.append(line)
            continue
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(stripped)

    if not order:
        return section

    bullets: list[str] = []
    for label in order:
        lines = groups[label]
        first = lines[0].lstrip("-*").strip()
        prose = _CITE_RE.sub("", first).strip().rstrip(".")
        display_head = prose.split(":", 1)[0] if ":" in prose else prose
        display_head = re.split(r"\bincludes\b", display_head, maxsplit=1, flags=re.I)[0].strip()
        all_cites: list[tuple[str, str]] = []
        for row in lines:
            all_cites.extend(_CITE_PARSE.findall(row))
        if len(lines) == 1 and len(all_cites) <= 2:
            bullets.append(f"- {first}")
            continue
        nums = sorted({int(n) for n, _ in all_cites})
        suffixes = [s for _, s in all_cites if s]
        suffix = f" {suffixes[0]}" if suffixes and len(set(suffixes)) == 1 else ""
        cite_block = f"[{', '.join(str(n) for n in nums)}{suffix}]" if nums else ""
        bullet = f"- {display_head}: {cite_block}".strip() if cite_block else f"- {display_head}"
        bullets.append(bullet.rstrip("."))
    body = "\n".join(other_lines + bullets)
    return body.strip()


def _sanitize_quantitative_table(text: str) -> str:
    """Drop qualitative filler rows from the Quantitative findings table."""
    section = _section(text, "Quantitative findings")
    if not section or "|" not in section:
        return text
    lines = section.splitlines()
    kept: list[str] = []
    header_done = False
    for line in lines:
        if not line.strip().startswith("|"):
            kept.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not header_done:
            kept.append(line)
            if cells and not all(set(c) <= {"-", " "} for c in cells):
                header_done = True
            continue
        if all(set(c) <= {"-", " "} for c in cells):
            kept.append(line)
            continue
        value_blob = " ".join(cells[1:3]) if len(cells) > 2 else " ".join(cells)
        if _HAS_MEASURED_NUMBER_RE.search(value_blob):
            kept.append(line)
            continue
        if _QUALITATIVE_METRIC_RE.search(value_blob) and not _HAS_MEASURED_NUMBER_RE.search(line):
            continue
        kept.append(line)
    cleaned = "\n".join(kept).strip()
    if cleaned == section.strip():
        return text
    return _replace_section(text, "Quantitative findings", cleaned)


def _sanitize_decision_thresholds(text: str) -> str:
    rule = _section(text, "Decision rule")
    if not rule:
        return text
    lines = rule.splitlines()
    out: list[str] = []
    in_empirical = False
    for line in lines:
        low = line.lower()
        if re.match(r"^#{2,3}\s+empirical", line, re.I):
            in_empirical = True
            out.append(line)
            continue
        if re.match(r"^#{2,3}\s+engineering", line, re.I):
            in_empirical = False
            out.append(line)
            continue
        if in_empirical and _THRESHOLD_LINE_RE.search(line) and not _HEURISTIC_MARKERS.search(line):
            if _CITE_RE.search(line):
                out.append(f"{line.rstrip()} *(Re-benchmark on your domain before treating as a universal cutoff.)*")
            else:
                out.append(
                    f"- **Heuristic (no measured threshold in sources — re-benchmark on your workload):** "
                    f"{_strip_bullet(line)}"
                )
            continue
        if in_empirical and re.search(r"\d+(?:\.\d+)?\s*%", line) and not _CITE_RE.search(line):
            if _HEURISTIC_MARKERS.search(line):
                out.append(line)
            else:
                out.append(
                    f"- **Heuristic (not a universal law):** {_strip_bullet(line)} "
                    f"Re-benchmark before applying to production."
                )
            continue
        out.append(line)
    new_rule = "\n".join(out).strip()
    if new_rule == rule.strip():
        return text
    return _replace_section(text, "Decision rule", new_rule)


def _remove_matching_subsections(body: str, heading_re: re.Pattern[str]) -> str:
    lines = body.splitlines()
    out: list[str] = []
    skip = False
    skip_level = 0
    for line in lines:
        if re.match(r"^#{3,4}\s+", line):
            level = len(re.match(r"^(#+)", line).group(1))  # type: ignore[union-attr]
            if heading_re.match(line):
                skip = True
                skip_level = level
                continue
            if skip and level <= skip_level:
                skip = False
        if skip:
            continue
        out.append(line)
    return "\n".join(out).strip()


def _extract_subsection(body: str, heading_re: re.Pattern[str]) -> str:
    lines = body.splitlines()
    out: list[str] = []
    capture = False
    level = 0
    for line in lines:
        if re.match(r"^#{3,4}\s+", line):
            lvl = len(re.match(r"^(#+)", line).group(1))  # type: ignore[union-attr]
            if heading_re.match(line):
                capture = True
                level = lvl
                continue
            if capture and lvl <= level:
                break
        if capture:
            out.append(line)
    return "\n".join(out).strip()


def _remove_subsection(body: str, heading_re: re.Pattern[str]) -> str:
    lines = body.splitlines()
    out: list[str] = []
    skip = False
    skip_level = 0
    for line in lines:
        if re.match(r"^#{3,4}\s+", line):
            lvl = len(re.match(r"^(#+)", line).group(1))  # type: ignore[union-attr]
            if heading_re.match(line):
                skip = True
                skip_level = lvl
                continue
            if skip and lvl <= skip_level:
                skip = False
        if skip:
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _bullet_lines(block: str) -> list[str]:
    lines: list[str] = []
    for line in (block or "").splitlines():
        t = line.strip()
        if not t:
            continue
        if t.startswith(("-", "*")):
            lines.append(t if t.startswith("-") else f"- {t[1:].strip()}")
        elif not t.startswith("#"):
            lines.append(f"- {t}")
    return lines


def _strip_bullet(line: str) -> str:
    return re.sub(r"^[-*]\s+", "", (line or "").strip())


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


def _insert_before_section(markdown: str, heading: str, block: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.I | re.M)
    match = pattern.search(markdown)
    if not match:
        return f"{markdown.rstrip()}\n\n{block.strip()}\n"
    return markdown[: match.start()] + block.strip() + "\n\n" + markdown[match.start() :]
