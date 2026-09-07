"""Programmatic validation of Tier-C quality rules.

Tier-C rules are structural/semantic rules that CAN be checked programmatically
but were previously only enforced via prompt instructions (prompt-only controls).

This module promotes them to enforceable gates to catch violations that slip
through LLM compliance.

Key Tier-C Rules:
1. Anti-compositing: Worked example must cite ONE source for end-to-end workflow
2. Per-dimension enforcement: Each comparison dimension gets separate subsection
3. Conceptual accuracy: Technical mechanisms verified against source quotes
4. Worked example source consistency: All steps from same evaluation

Note: Conceptual accuracy (#3) is partially checked via citation cross-reference;
full semantic verification requires embedding/LLM similarity checks (future).
"""

from __future__ import annotations

import re
from typing import Any


def check_worked_example_compositing(memo_markdown: str) -> dict[str, Any]:
    """Check if Worked example cites multiple sources without Composite label.
    
    Tier-C Rule: W1 (Anti-compositing)
    
    Returns:
        dict with:
            - violation: bool
            - severity: "error" | "warning" | "ok"
            - message: str (user-facing explanation)
            - details: dict with citation_count, has_composite_label, etc.
    """
    # Extract Worked example section
    match = re.search(r'##\s+Worked example\s*\n(.*?)(?=\n##\s+|\Z)', memo_markdown, re.DOTALL | re.I)
    if not match:
        return {
            "violation": False,
            "severity": "ok",
            "message": "No Worked example section found (OK if not required)",
            "details": {"has_section": False}
        }
    
    section_text = match.group(1)
    
    # Check for Composite label (blockquote with **Composite** marker)
    has_composite_label = bool(re.search(r'>\s*\*\*Composite\*\*', section_text, re.I))
    
    # Extract citation numbers
    # Pattern: [n], [n repo], [n arXiv], etc.
    citation_markers = re.findall(r'\[(\d+)(?:\s+[A-Za-z]+)?\]', section_text)
    unique_citations = set(int(n) for n in citation_markers)
    
    # Also check for multi-citation markers like [5, 6, 7]
    multi_markers = re.findall(r'\[(\d+(?:\s*,\s*\d+)+)\]', section_text)
    for marker in multi_markers:
        numbers = re.findall(r'\d+', marker)
        unique_citations.update(int(n) for n in numbers)
    
    citation_count = len(unique_citations)
    
    # Violation: >= 2 sources without Composite label
    violation = citation_count >= 2 and not has_composite_label
    
    return {
        "violation": violation,
        "severity": "error" if violation else "ok",
        "message": (
            f"Worked example cites {citation_count} sources {list(unique_citations)} "
            f"without Composite label. This violates anti-compositing rule (W1)."
            if violation
            else f"Worked example OK: {citation_count} source(s), composite_labeled={has_composite_label}"
        ),
        "details": {
            "has_section": True,
            "citation_count": citation_count,
            "cited_sources": sorted(unique_citations),
            "has_composite_label": has_composite_label
        }
    }


def check_per_dimension_subsections(
    memo_markdown: str,
    required_dimensions: list[str] | None = None
) -> dict[str, Any]:
    """Check if Detailed analysis has separate subsection for each dimension.
    
    Tier-C Rule: W3 (Per-dimension subsection enforcement)
    
    Args:
        memo_markdown: Full memo text
        required_dimensions: List of dimension names (from dossier labels)
                            If None, skips dimension matching
    
    Returns:
        dict with:
            - violation: bool
            - severity: "error" | "warning" | "ok"
            - message: str
            - details: dict with found_subsections, missing_dimensions, etc.
    """
    # Extract Detailed analysis section
    match = re.search(r'##\s+Detailed analysis\s*\n(.*?)(?=\n##\s+|\Z)', memo_markdown, re.DOTALL | re.I)
    if not match:
        return {
            "violation": True,
            "severity": "error",
            "message": "Missing '## Detailed analysis' section",
            "details": {"has_section": False, "found_subsections": []}
        }
    
    section_text = match.group(1)
    
    # Extract ### subsections
    subsections = re.findall(r'###\s+(.+)', section_text)
    subsection_count = len(subsections)
    
    # Check for forbidden generic subsections
    forbidden_generic = [
        "overview", "approaches", "techniques", "methods", "key findings",
        "comparison", "analysis", "summary", "details"
    ]
    found_generic = [
        sub for sub in subsections
        if any(generic in sub.lower() for generic in forbidden_generic)
    ]
    
    violation = False
    severity = "ok"
    message_parts = []
    
    # Check minimum subsections (at least 2 for comparison questions)
    if subsection_count < 2 and required_dimensions and len(required_dimensions) >= 2:
        violation = True
        severity = "error"
        message_parts.append(f"Only {subsection_count} subsection(s), expected >= 2")
    
    # Check for forbidden generic subsections
    if found_generic:
        violation = True
        severity = "warning"
        message_parts.append(f"Generic subsections found: {found_generic}")
    
    # Check specific dimensions (if provided)
    missing_dimensions = []
    if required_dimensions:
        for dim in required_dimensions:
            # Fuzzy match: dimension name appears in any subsection (lowercase, contains)
            if not any(dim.lower() in sub.lower() for sub in subsections):
                missing_dimensions.append(dim)
        
        if missing_dimensions:
            violation = True
            severity = "error"
            message_parts.append(f"Missing dimension subsections: {missing_dimensions}")
    
    message = "; ".join(message_parts) if message_parts else f"Per-dimension subsections OK: {subsection_count} found"
    
    return {
        "violation": violation,
        "severity": severity,
        "message": message,
        "details": {
            "has_section": True,
            "subsection_count": subsection_count,
            "found_subsections": subsections,
            "found_generic": found_generic,
            "missing_dimensions": missing_dimensions,
            "required_dimensions": required_dimensions or []
        }
    }


def check_citation_stacking_excessive(memo_markdown: str, threshold_per_1000_words: float = 3.0) -> dict[str, Any]:
    """Check for excessive citation stacking (sentences citing 3+ distinct sources).
    
    Not a hard violation, but a quality indicator (often correlates with thin synthesis).
    
    Args:
        memo_markdown: Full memo text
        threshold_per_1000_words: Max stacking sentences per 1000 words (default 3)
    
    Returns:
        dict with violation, severity, message, details
    """
    # Count words
    words = re.findall(r'\S+', memo_markdown)
    word_count = len(words)
    
    # Split into sentences (simple heuristic)
    sentences = re.split(r'(?<=[.!?])\s+', memo_markdown)
    
    stacking_count = 0
    stacked_sentences = []
    
    for sentence in sentences:
        # Extract citation numbers from this sentence
        citation_markers = re.findall(r'\[([^\]]+)\]', sentence)
        if not citation_markers:
            continue
        
        cited_numbers = set()
        for marker in citation_markers:
            numbers = re.findall(r'\b(\d+)\b', marker)
            cited_numbers.update(int(n) for n in numbers)
        
        if len(cited_numbers) >= 3:
            stacking_count += 1
            # Truncate sentence for readability
            truncated = sentence[:100] + "..." if len(sentence) > 100 else sentence
            stacked_sentences.append(truncated)
    
    stacking_per_1000 = (stacking_count / max(word_count, 1)) * 1000
    violation = stacking_per_1000 > threshold_per_1000_words
    
    return {
        "violation": violation,
        "severity": "warning" if violation else "ok",
        "message": (
            f"Excessive citation stacking: {stacking_count} sentences cite 3+ sources "
            f"({stacking_per_1000:.1f} per 1000 words > {threshold_per_1000_words} threshold). "
            "This often indicates thin synthesis."
            if violation
            else f"Citation stacking OK: {stacking_per_1000:.1f} per 1000 words"
        ),
        "details": {
            "stacking_count": stacking_count,
            "word_count": word_count,
            "stacking_per_1000_words": stacking_per_1000,
            "threshold": threshold_per_1000_words,
            "examples": stacked_sentences[:3]  # Show up to 3 examples
        }
    }


def check_quantitative_findings_validity(memo_markdown: str) -> dict[str, Any]:
    """Check if Quantitative findings section (if present) has actual data rows.
    
    Optional section, but if present, should not be empty.
    
    Returns:
        dict with violation, severity, message, details
    """
    # Check if Quantitative findings section exists
    match = re.search(r'##\s+Quantitative findings\s*\n(.*?)(?=\n##\s+|\Z)', memo_markdown, re.DOTALL | re.I)
    if not match:
        return {
            "violation": False,
            "severity": "ok",
            "message": "No Quantitative findings section (OK if not required)",
            "details": {"has_section": False}
        }
    
    section_text = match.group(1)
    
    # Count table rows (simplified: lines starting with |)
    table_lines = [line for line in section_text.splitlines() if line.strip().startswith("|")]
    
    # Subtract header and separator (typically 2-3 rows)
    data_rows = max(0, len(table_lines) - 3)
    
    # Violation: section exists but no data
    violation = data_rows == 0
    
    return {
        "violation": violation,
        "severity": "warning" if violation else "ok",
        "message": (
            "Quantitative findings section exists but has no data rows. "
            "Either populate with actual numbers or omit the section."
            if violation
            else f"Quantitative findings OK: {data_rows} data row(s)"
        ),
        "details": {
            "has_section": True,
            "data_row_count": data_rows,
            "total_table_lines": len(table_lines)
        }
    }


def validate_memo_structure(
    memo_markdown: str,
    *,
    required_dimensions: list[str] | None = None,
    citation_stacking_threshold: float = 3.0
) -> dict[str, Any]:
    """Run all Tier-C structural validations on memo.
    
    Args:
        memo_markdown: Full memo markdown text
        required_dimensions: List of dimension names (from dossier) for per-dimension check
        citation_stacking_threshold: Max stacking per 1000 words (default 3.0)
    
    Returns:
        dict with:
            - overall_pass: bool (True if no errors, warnings OK)
            - error_count: int
            - warning_count: int
            - checks: dict of check_name -> check_result
            - summary_message: str
    """
    checks = {
        "worked_example_compositing": check_worked_example_compositing(memo_markdown),
        "per_dimension_subsections": check_per_dimension_subsections(memo_markdown, required_dimensions),
        "citation_stacking": check_citation_stacking_excessive(memo_markdown, citation_stacking_threshold),
        "quantitative_findings": check_quantitative_findings_validity(memo_markdown)
    }
    
    error_count = sum(1 for c in checks.values() if c["severity"] == "error")
    warning_count = sum(1 for c in checks.values() if c["severity"] == "warning")
    overall_pass = error_count == 0
    
    error_messages = [c["message"] for c in checks.values() if c["severity"] == "error"]
    warning_messages = [c["message"] for c in checks.values() if c["severity"] == "warning"]
    
    if overall_pass and warning_count == 0:
        summary_message = "All Tier-C structural checks passed"
    elif overall_pass:
        summary_message = f"{warning_count} warning(s): {'; '.join(warning_messages)}"
    else:
        summary_message = f"{error_count} error(s): {'; '.join(error_messages)}"
    
    return {
        "overall_pass": overall_pass,
        "error_count": error_count,
        "warning_count": warning_count,
        "checks": checks,
        "summary_message": summary_message,
        "errors": error_messages,
        "warnings": warning_messages
    }
