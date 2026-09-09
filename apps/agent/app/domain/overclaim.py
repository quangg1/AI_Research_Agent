"""
Overclaim language detection and softening for research memos.

Detects absolute claims not supported by evidence and suggests softer alternatives.
"""

from __future__ import annotations
import re
from typing import Any

# Absolute terms that should be softened unless formally proven
ABSOLUTE_TERMS = {
    # Completeness claims
    r"\bcompletely eliminat(e|es|ed|ing)\b": "largely reduces",
    r"\bcompletely remov(e|es|ed|ing)\b": "largely removes",
    r"\bcompletely prevent(s|ed|ing)?\b": "largely prevents",
    r"\bcompletely solv(e|es|ed|ing)\b": "largely addresses",
    
    # Universality claims
    r"\balways succeed(s|ed)?\b": "typically succeeds",
    r"\balways work(s|ed)?\b": "generally works",
    r"\balways achiev(e|es|ed)\b": "typically achieves",
    r"\bnever fail(s|ed)?\b": "rarely fails",
    r"\bnever produc(e|es|ed)\b": "rarely produces",
    
    # Guarantee claims (unless mathematical/formal)
    r"\bguarantees? that every\b": "ensures most",
    r"\bguarantees? complete\b": "provides strong",
    r"\bguarantees? perfect\b": "provides high",
    
    # Absolute quantifiers
    r"\bevery single\b": "most",
    r"\ball possible\b": "many",
    r"\bno exceptions?\b": "few exceptions",
    r"\bwithout exception\b": "with rare exceptions",
}

# Formal/mathematical contexts where absolutes are allowed
FORMAL_CONTEXTS = [
    r"differential privacy guarantees",
    r"formal verification guarantees",
    r"mathematical guarantee",
    r"provably",
    r"theorem \d+",
    r"epsilon\s*=",
    r"with probability 1",
]


def contains_formal_context(text: str, absolute_match_start: int, absolute_match_end: int) -> bool:
    """
    Check if the absolute term appears in a formal/mathematical context.
    
    Looks for formal indicators within ±100 characters of the absolute term.
    """
    window_start = max(0, absolute_match_start - 100)
    window_end = min(len(text), absolute_match_end + 100)
    context_window = text[window_start:window_end].lower()
    
    for formal_pattern in FORMAL_CONTEXTS:
        if re.search(formal_pattern, context_window, re.I):
            return True
    return False


def detect_overclaims(text: str, min_confidence: float = 0.7) -> list[dict[str, Any]]:
    """
    Detect potential overclaims in research memo text.
    
    Returns list of issues with location, original text, and suggested replacement.
    """
    issues: list[dict[str, Any]] = []
    
    for pattern, replacement in ABSOLUTE_TERMS.items():
        for match in re.finditer(pattern, text, re.I):
            # Skip if in formal context
            if contains_formal_context(text, match.start(), match.end()):
                continue
            
            issues.append({
                "location": match.start(),
                "original": match.group(0),
                "suggested": replacement,
                "pattern": pattern,
                "confidence": min_confidence,
                "context": text[max(0, match.start() - 50):min(len(text), match.end() + 50)],
            })
    
    return issues


def soften_overclaims(text: str, aggressive: bool = False) -> tuple[str, list[dict]]:
    """
    Soften absolute language in research memo.
    
    Args:
        text: Original memo text
        aggressive: If True, apply all replacements. If False, only high-confidence ones.
    
    Returns:
        (softened_text, list of changes made)
    """
    changes: list[dict] = []
    result = text
    
    for pattern, replacement in ABSOLUTE_TERMS.items():
        matches = list(re.finditer(pattern, result, re.I))
        
        for match in reversed(matches):  # Reverse to preserve positions
            # Skip if in formal context
            if contains_formal_context(result, match.start(), match.end()):
                continue
            
            original = match.group(0)
            
            # Preserve original capitalization pattern
            if original[0].isupper():
                softened = replacement.capitalize()
            else:
                softened = replacement
            
            # Replace
            result = result[:match.start()] + softened + result[match.end():]
            
            changes.append({
                "location": match.start(),
                "original": original,
                "replacement": softened,
                "pattern": pattern,
            })
    
    return result, changes


def validate_claim_strength(claim: str, evidence_count: int = 1, is_formal: bool = False) -> dict[str, Any]:
    """
    Validate if claim strength is appropriate for evidence.
    
    Args:
        claim: The claim text
        evidence_count: Number of supporting citations
        is_formal: Whether claim includes formal/mathematical proof
    
    Returns:
        Validation result with issues and recommendations
    """
    issues = []
    
    # Check for absolute language
    overclaims = detect_overclaims(claim)
    
    if overclaims and not is_formal:
        if evidence_count == 1:
            issues.append(
                f"Absolute claim '{overclaims[0]['original']}' supported by only {evidence_count} citation. "
                f"Consider: '{overclaims[0]['suggested']}'"
            )
        elif evidence_count < 3:
            issues.append(
                f"Strong claim '{overclaims[0]['original']}' supported by only {evidence_count} citations. "
                "Consider softening or adding more evidence."
            )
    
    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "overclaims": overclaims,
        "evidence_count": evidence_count,
        "is_formal": is_formal,
    }
