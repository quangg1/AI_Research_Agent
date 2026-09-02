"""Memo quality checks for automatic regeneration triggers.

Detects quality issues that should block publication and trigger regeneration:
- Duplicate quotes across sections
- Citation stacking (3+ sources in one sentence)
- Empty filler sentences
"""

from __future__ import annotations

import re
from typing import Any


def check_memo_quality(body_markdown: str, *, evidence: list[dict] | None = None) -> dict[str, Any]:
    """Check memo quality and return issues that should trigger regeneration.
    
    Returns:
        dict with:
        - should_regenerate: bool
        - issues: list of str describing problems
        - duplicate_quote_ratio: float
        - citation_stacking_count: int
        - source_saturation_count: int
        - empty_filler_count: int
        - template_placeholder_count: int
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
    
    should_regenerate = (
        duplicate_ratio > 0.40
        or stacking_count >= 2
        or saturation_count >= 2
        or filler_count >= 1
        or placeholder_count > 0
    )
    
    return {
        "should_regenerate": should_regenerate,
        "issues": issues,
        "duplicate_quote_ratio": duplicate_ratio,
        "citation_stacking_count": stacking_count,
        "source_saturation_count": saturation_count,
        "empty_filler_count": filler_count,
        "template_placeholder_count": placeholder_count,
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
        r'\[?\?\]',  # Literal [?] markers
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
