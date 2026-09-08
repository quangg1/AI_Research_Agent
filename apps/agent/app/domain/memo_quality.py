"""Memo quality checks for automatic regeneration triggers.

Detects quality issues that should block publication and trigger regeneration:
- Duplicate quotes across sections
- Citation stacking (3+ sources in one sentence)
- Empty filler sentences
"""

from __future__ import annotations

import re
from typing import Any

from app.conf.thresholds import CoverageThresholds, QualityThresholds

# Anchored to a real citation-marker shape ("2", "3 peer", "2, 5 peer") —
# NOT "any bracketed text". A bare r"\[([^\]]+)\]" also matched markdown link
# titles like "[Is Model Collapse Inevitable? ...](url)" produced by
# bind_markdown_to_ledger's References section: since a title contains no
# leading digit, _marker_numbers parsed zero citation numbers out of it, and
# repl()'s `if not kept: return ""` silently deleted the entire title,
# leaving a bare "(url) — `url`" behind — this is exactly the "References
# lost every title" bug seen in production. Mirrors report_integrity.CITE_RE.
_MARKER_RE = re.compile(r"\[(\d+(?:\s+[A-Za-z]+)?(?:\s*,\s*\d+(?:\s+[A-Za-z]+)?)*)\]")
_MARKER_NUM_RE = re.compile(r"(\d+)(\s+[A-Za-z]+)?")


def _marker_numbers(marker_text: str) -> list[tuple[int, str]]:
    """Parse '1, 2 peer, 3' into [(1, ''), (2, ' peer'), (3, '')]."""
    out: list[tuple[int, str]] = []
    for piece in marker_text.split(","):
        m = _MARKER_NUM_RE.match(piece.strip())
        if m:
            out.append((int(m.group(1)), m.group(2) or ""))
    return out


def declutter_citations(
    body: str,
    *,
    max_distinct_per_sentence: int = 2,
    max_per_source_per_paragraph: int = 2,
) -> tuple[str, int]:
    """Deterministic fix for citation stacking and source saturation.

    Both are pure citation-*marker-placement* issues, not content problems —
    the underlying claim is still true whether it carries 2 citations or 5.
    Fixing them by asking the writer LLM to regenerate the whole memo (up to
    ~3 calls per attempt, up to 2 attempts) is expensive and, per observed
    runs, unreliable — the model doesn't reliably fix its own over-citing.
    This trims markers directly: at most `max_distinct_per_sentence` distinct
    sources per sentence, at most `max_per_source_per_paragraph` repeats of
    the same source within one paragraph. The claims and remaining citations
    are untouched — this only declutters redundant citation decoration.
    
    Skips "Source quality" and "References" sections where citation lists
    should remain intact.

    Returns (new_body, markers_changed_count).
    """
    # Split by ## sections to identify and skip Source quality / References
    sections = re.split(r"(^##\s+.+$)", body or "", flags=re.M)
    out_sections: list[str] = []
    skip_next = False
    
    for i, section in enumerate(sections):
        # Check if this is a section header
        if re.match(r"^##\s+(Source quality|References)\s*$", section, re.I):
            skip_next = True
            out_sections.append(section)
            continue
        
        # If previous header was Source quality/References, skip processing this section
        if skip_next and i > 0:
            # Check if we hit another ## header (end of skip section)
            if re.match(r"^##\s+", section, re.M):
                skip_next = False
            else:
                out_sections.append(section)
                continue
        
        # Reset skip flag if we hit a new section
        if re.match(r"^##\s+", section):
            skip_next = False
        
        # Process this section normally
        if not section.strip():
            out_sections.append(section)
            continue
    
    # Rejoin to process non-skipped sections
    changed_total = 0
    processed: list[str] = []
    
    for section_text in out_sections:
        # Check if this section should be skipped (between Source quality/References headers)
        # For simplicity, process paragraph by paragraph within non-skipped sections
        paragraphs = re.split(r"(\n\s*\n)", section_text)
        section_parts: list[str] = []
        
        for part in paragraphs:
            if not part.strip():
                section_parts.append(part)
                continue
            seen_in_paragraph: dict[int, int] = {}
            sentences = re.split(r"(?<=[.!?])(\s+)", part)
            new_sentences: list[str] = []
            for chunk in sentences:
                if not chunk.strip() or _MARKER_RE.search(chunk) is None:
                    new_sentences.append(chunk)
                    continue
                all_nums: list[int] = []
                for m in _MARKER_RE.finditer(chunk):
                    for n, _tier in _marker_numbers(m.group(1)):
                        if n not in all_nums:
                            all_nums.append(n)
                allowed = set(all_nums[:max_distinct_per_sentence])

                def repl(m: re.Match) -> str:
                    nonlocal changed_total
                    nums = _marker_numbers(m.group(1))
                    kept: list[tuple[int, str]] = []
                    for n, tier in nums:
                        if n not in allowed:
                            continue
                        c = seen_in_paragraph.get(n, 0)
                        if c >= max_per_source_per_paragraph:
                            continue
                        seen_in_paragraph[n] = c + 1
                        kept.append((n, tier))
                    if len(kept) != len(nums):
                        changed_total += 1
                    if not kept:
                        return ""
                    return "[" + ", ".join(f"{n}{tier}" for n, tier in kept) + "]"

                new_sentences.append(_MARKER_RE.sub(repl, chunk))
            new_part = "".join(new_sentences)
            # Tidy spacing left by a fully-removed marker ("text  ." / "text  and").
            new_part = re.sub(r"[ \t]+([.,;:])", r"\1", new_part)
            new_part = re.sub(r"[ \t]{2,}", " ", new_part)
            section_parts.append(new_part)
        processed.append("".join(section_parts))
    
    return "".join(processed), changed_total


def check_memo_quality(
    body_markdown: str, 
    *, 
    evidence: list[dict] | None = None,
    coverage: dict | None = None,
) -> dict[str, Any]:
    """Check memo quality and return issues that should trigger regeneration.
    
    NOW PRODUCTION-AWARE: Only triggers regeneration for GENERATION issues.
    Retrieval issues (coverage, missing dimensions) should trigger research loop, not regeneration.
    
    Returns:
        dict with:
        - should_regenerate: bool (only for generation issues)
        - issues: list of str describing problems
        - is_retrieval_issue: bool (if issues are unfixable by regeneration)
        - duplicate_quote_ratio: float
        - citation_stacking_count: int
        - source_saturation_count: int
        - empty_filler_count: int
        - template_placeholder_count: int
        - composite_worked_example_count: int
    """
    issues: list[str] = []
    
    # Check 1: Duplicate quotes across sections
    duplicate_ratio, duplicate_details = _detect_duplicate_quotes_across_sections(body_markdown)
    if duplicate_ratio > 0.40:
        issues.append(
            f"Duplicate quotes across sections: {int(duplicate_ratio * 100)}% overlap. "
            f"Same quotes appear in multiple dimension subsections. {duplicate_details}"
        )
    
    # Check 2: Citation stacking (3+ sources in one sentence)
    stacking_count, stacking_examples = _detect_citation_stacking(body_markdown)
    if stacking_count > 0:
        issues.append(
            f"Citation stacking detected: {stacking_count} sentences cite 3+ sources. "
            f"Examples: {'; '.join(stacking_examples[:3])}"
        )
    
    # Check 2b: Single-source saturation (same source 3+ times in one paragraph)
    saturation_count, saturation_examples = _detect_source_saturation(body_markdown)
    if saturation_count > 0:
        issues.append(
            f"Source saturation detected: {saturation_count} paragraphs cite same source 3+ times. "
            f"Examples: {'; '.join(saturation_examples[:3])}"
        )
    
    # Check 3: Empty filler sentences
    filler_count, filler_examples = _detect_empty_filler(body_markdown)
    if filler_count > 0:
        issues.append(
            f"Empty filler sentences detected: {filler_count} occurrences. "
            f"Examples: {'; '.join(filler_examples[:3])}"
        )
    
    # Check 4: Template placeholders
    placeholder_count, placeholder_examples = _detect_template_placeholders(body_markdown)
    if placeholder_count > 0:
        issues.append(
            f"Template placeholders leaked: {placeholder_count} occurrences. "
            f"Examples: {'; '.join(placeholder_examples[:3])}"
        )
    
    # Check 5: Composite worked example without label
    # NOTE: Don't trigger regeneration - will be labeled deterministically
    # in enforce_report_integrity to avoid regeneration loop
    composite_count, composite_examples = _detect_composite_worked_example(body_markdown)
    if composite_count > 0:
        issues.append(
            f"Worked example cites multiple sources without 'Composite' label. "
            f"{'; '.join(composite_examples)}"
        )
    
    # Calculate base regeneration trigger (generation issues only)
    should_regenerate = (
        duplicate_ratio > 0.40
        or stacking_count >= 2
        or saturation_count >= 2
        or filler_count >= 1
        or placeholder_count > 0
    )
    
    # NEW: Check if this is actually a retrieval issue (production-aware)
    is_retrieval_issue = False
    if coverage:
        is_retrieval_issue = _is_retrieval_issue(coverage)
        if is_retrieval_issue:
            # Don't regenerate for retrieval issues - they need research loop
            should_regenerate = False
            issues.insert(0, "⚠️ Coverage/evidence issues detected - requires research loop, not regeneration")
    
    return {
        "should_regenerate": should_regenerate,
        "issues": issues,
        "is_retrieval_issue": is_retrieval_issue,
        "duplicate_quote_ratio": duplicate_ratio,
        "citation_stacking_count": stacking_count,
        "source_saturation_count": saturation_count,
        "empty_filler_count": filler_count,
        "template_placeholder_count": placeholder_count,
        "composite_worked_example_count": composite_count,
    }


def _detect_duplicate_quotes_across_sections(body: str) -> tuple[float, str]:
    """Detect if the same quotes appear in multiple ### subsections under ## Detailed analysis."""
    # Extract all ### subsections under ## Detailed analysis
    analysis_match = re.search(r"## Detailed analysis\s*\n(.*?)(?=\n##|$)", body, re.DOTALL)
    if not analysis_match:
        return (0.0, "")
    
    analysis_section = analysis_match.group(1)
    subsections = re.split(r"###\s+", analysis_section)
    if len(subsections) < 2:
        return (0.0, "")
    
    # Extract quoted text from each subsection (text between curly quotes)
    section_quotes: list[set[str]] = []
    for subsection in subsections[1:]:  # Skip first element (text before first ###)
        quotes = re.findall(r'"([^"]+)"', subsection)
        if quotes:
            # Normalize quotes (lowercase, remove extra whitespace)
            normalized = {" ".join(q.lower().split())[:100] for q in quotes if len(q) > 40}
            section_quotes.append(normalized)
    
    if len(section_quotes) < 2:
        return (0.0, "")
    
    # Find quotes that appear in multiple sections
    all_quotes = set().union(*section_quotes)
    if not all_quotes:
        return (0.0, "")
    
    duplicate_quotes: set[str] = set()
    for quote in all_quotes:
        appearances = sum(1 for sq in section_quotes if quote in sq)
        if appearances >= 2:
            duplicate_quotes.add(quote)
    
    if not duplicate_quotes:
        return (0.0, "")
    
    duplicate_ratio = len(duplicate_quotes) / len(all_quotes)
    
    # Generate details
    examples = list(duplicate_quotes)[:2]
    details = f"Repeated quotes: {'; '.join(f'"{q[:60]}..."' for q in examples)}"
    
    return (duplicate_ratio, details)


def _detect_citation_stacking(body: str) -> tuple[int, list[str]]:
    """Detect sentences that cite 3+ distinct sources (citation stacking).
    
    Note: Citing 2 sources together is acceptable (e.g., "Evidence from [1, 2]").
    Only 3+ sources in a single citation is considered stacking.
    """
    # Match sentences with citation markers like [1], [2, 3], [4 peer]
    sentences = re.split(r'(?<=[.!?])\s+', body)
    
    stacking_count = 0
    examples: list[str] = []
    
    for sentence in sentences:
        # Find all citation markers [n] or [n, m, ...] or [n peer, m repo, ...]
        citation_markers = re.findall(r'\[([^\]]+)\]', sentence)
        if not citation_markers:
            continue
        
        # Extract distinct citation numbers
        cited_numbers: set[int] = set()
        for marker in citation_markers:
            # Extract all numbers from the marker (e.g., "1, 2, 3 peer" -> [1, 2, 3])
            numbers = re.findall(r'\b(\d+)\b', marker)
            cited_numbers.update(int(n) for n in numbers)
        
        # Only flag if 3 or more distinct sources in a single sentence
        if len(cited_numbers) >= 3:
            stacking_count += 1
            # Truncate sentence for example
            example = sentence[:120].strip()
            if len(sentence) > 120:
                example += "..."
            examples.append(example)
    
    return (stacking_count, examples[:5])


def _detect_source_saturation(body: str) -> tuple[int, list[str]]:
    """Detect paragraphs where the same source is cited 3+ times (source saturation).
    
    This catches over-reliance on a single source within a paragraph, which indicates
    insufficient evidence diversity or quote-dumping from one paper.
    """
    # Split by double newlines to get paragraphs
    paragraphs = re.split(r'\n\s*\n', body)
    
    saturation_count = 0
    examples: list[str] = []
    
    for para in paragraphs:
        if len(para.strip()) < 50:  # Skip very short paragraphs
            continue
        
        # Find all citation markers [n] or [n peer] etc.
        citation_markers = re.findall(r'\[([^\]]+)\]', para)
        if not citation_markers:
            continue
        
        # Count occurrences of each source number
        source_counts: dict[int, int] = {}
        for marker in citation_markers:
            numbers = re.findall(r'\b(\d+)\b', marker)
            for num_str in numbers:
                num = int(num_str)
                source_counts[num] = source_counts.get(num, 0) + 1
        
        # Check if any single source appears 3+ times in this paragraph
        for source_num, count in source_counts.items():
            if count >= 3:
                saturation_count += 1
                if len(examples) < 5:
                    para_preview = para[:100].replace('\n', ' ').strip()
                    examples.append(f"Paragraph cites [{source_num}] {count} times: {para_preview}...")
                break  # Only count each paragraph once
    
    return (saturation_count, examples)


def _detect_empty_filler(body: str) -> tuple[int, list[str]]:
    """Detect empty filler sentences like 'X is carried by the collected sources'."""
    filler_patterns = [
        r"is carried by the collected sources",
        r"are carried by the collected sources",
        r"is supported by the collected sources",
        r"are supported by the collected sources",
        r"appears in the collected sources",
        r"appear in the collected sources",
        r"is present in the evidence",
        r"are present in the evidence",
    ]
    
    filler_count = 0
    examples: list[str] = []
    
    for pattern in filler_patterns:
        matches = re.finditer(pattern, body, re.I)
        for match in matches:
            filler_count += 1
            # Extract the full sentence containing the filler
            start = max(0, match.start() - 100)
            end = min(len(body), match.end() + 100)
            context = body[start:end]
            sentence_match = re.search(r'([^.!?]*' + re.escape(match.group(0)) + r'[^.!?]*[.!?])', context)
            if sentence_match:
                examples.append(sentence_match.group(1).strip())
            else:
                examples.append(context.strip()[:120])
    
    return (filler_count, examples[:5])


def _detect_template_placeholders(body: str) -> tuple[int, list[str]]:
    """Detect unresolved template placeholders and malformed fields."""
    placeholder_patterns = [
        # Common placeholder patterns
        r'REVISIT\s+IF:?\s*\w*',
        r'TODO:?\s+\w+',
        r'FIXME:?\s+\w+',
        r'XXX:?\s+\w+',
        # Literal [?] unresolved-citation markers. The bracket must be
        # present — an earlier `\[?\?\]` (optional bracket) also matched any
        # real title ending in "?" right before a markdown link's "]", e.g.
        # "...Which Multi-AI Agent Framework is Best?](url)". That false
        # positive wasted 2 full LLM report-regeneration cycles on one real
        # run chasing an "issue" a rewrite could never fix (the title is
        # baked into the deterministic References list, not writer prose).
        r'\[\?\]',
        r'\[n\]',  # Unresolved citation placeholders
        r'\{[A-Z_]+\}',  # Template variables like {FIELD_NAME}
        r'<[A-Z_]+>',  # Template variables like <FIELD_NAME>
        r'chưa xác định',  # Vietnamese "not determined" placeholder
        # Dangling field patterns
        r'\bwhen:\s*$',  # "when:" with no value
        r'\bwhere:\s*$',
        r'\bif:\s*$',
    ]
    
    placeholder_count = 0
    examples: list[str] = []
    
    for pattern in placeholder_patterns:
        matches = re.finditer(pattern, body, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            placeholder_count += 1
            # Extract context around the placeholder
            start = max(0, match.start() - 60)
            end = min(len(body), match.end() + 60)
            context = body[start:end].replace('\n', ' ').strip()
            if len(context) > 100:
                context = context[:100] + "..."
            examples.append(f"'{match.group(0)}' in: {context}")
    
    # Check for truly empty sections (## followed directly by ## with no content)
    # But allow ##\n\n### (subsections are fine)
    empty_section_pattern = r'##\s+[A-Za-z\s]+\s*\n\s*\n+\s*##\s+[A-Za-z]'
    empty_matches = re.finditer(empty_section_pattern, body, re.MULTILINE)
    for match in empty_matches:
        # Make sure it's not just a section followed by a subsection
        section_text = match.group(0)
        if '###' not in section_text:  # Only flag if there's no subsection marker
            placeholder_count += 1
            context = match.group(0)[:100].replace('\n', ' ')
            examples.append(f"Empty section in: {context}")
    
    return (placeholder_count, examples[:5])


def _detect_composite_worked_example(body: str) -> tuple[int, list[str]]:
    """Detect Worked example sections citing ≥2 sources without 'Composite' label.
    
    Returns: (violation_count, examples)
    """
    # Extract Worked example section if present
    match = re.search(r'##\s+Worked example\s*\n(.*?)(?=\n##\s+|\Z)', body, re.DOTALL | re.I)
    if not match:
        return (0, [])
    
    section_text = match.group(1)
    
    # Check if "Composite" label is present
    has_composite_label = bool(re.search(r'\bComposite\b', section_text, re.I))
    
    # Count distinct citation numbers in the section
    citation_numbers = set()
    for m in _MARKER_RE.finditer(section_text):
        for n, _tier in _marker_numbers(m.group(1)):
            citation_numbers.add(n)
    
    # Violation: ≥2 distinct citations without "Composite" label
    if len(citation_numbers) >= 2 and not has_composite_label:
        preview = section_text[:150].replace('\n', ' ').strip()
        return (1, [f"Worked example cites {len(citation_numbers)} sources without 'Composite' label: {preview}..."])
    
    return (0, [])


def _is_retrieval_issue(coverage: dict) -> bool:
    """Determine if quality issues stem from retrieval (unfixable by regeneration).
    
    Production-aware: Don't waste regenerations on retrieval problems.
    
    Retrieval issues:
    - Low coverage (<65%)
    - Missing critical dimensions
    - No primary sources
    
    These should trigger RESEARCH LOOP (more retrieval), not QUALITY LOOP (regeneration).
    
    Args:
        coverage: Coverage dict from critic with depth_score, critical_gaps, etc.
    
    Returns:
        True if issues are retrieval-related (don't regenerate)
        False if issues are generation-related (can regenerate)
    """
    if not coverage:
        return False
    
    # Check coverage level
    depth_score = coverage.get("depth_score") or {}
    must_answer = depth_score.get("must_answer") or {}
    must_pct = must_answer.get("pct") or 0
    
    # Low coverage is a retrieval issue
    if must_pct < CoverageThresholds.MUST_COVERAGE_GOOD:
        return True
    
    # Missing critical dimensions is a retrieval issue
    critical_gaps = coverage.get("critical_gaps") or []
    if critical_gaps:
        return True
    
    # No primary sources is a retrieval issue
    primary_sources = coverage.get("primary_sources") or 0
    if primary_sources == 0:
        return True
    
    # Otherwise, it's a generation issue (can be fixed by regeneration)
    return False
