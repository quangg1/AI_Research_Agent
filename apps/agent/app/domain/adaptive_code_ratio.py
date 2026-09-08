"""Adaptive code ratio based on query intent.

Addresses Issue #2: Domain-balance 40% code cap is too rigid.
Implementation-centric queries legitimately need higher code ratio.

Strategy:
- Theory/benchmark queries: 30% code (prefer papers)
- General comparison: 40% code (balanced)
- Implementation queries: 55% code (code is primary evidence)
- Code-focused implementation: 65% code (repos are main source)

Uses query_type from routing_policy/briefing classification.
"""

from __future__ import annotations


def adaptive_code_ratio(query_type: str | None, query: str = "") -> float:
    """Calculate max code ratio based on query classification.
    
    Args:
        query_type: From routing_policy/briefing (e.g., "implementation", "comparison", "theory")
        query: Original query text (fallback for keyword detection)
    
    Returns:
        float: Max code ratio (0.0-1.0)
    """
    # Map query types to code ratios
    # Conservative defaults: prefer academic sources unless clearly implementation-focused
    
    if not query_type:
        # Fallback to keyword detection if no classification
        query_lower = query.lower()
        if any(kw in query_lower for kw in ["implement", "code", "repo", "github", "library", "package", "framework"]):
            query_type = "implementation"
        elif any(kw in query_lower for kw in ["theory", "mathematical", "proof", "theorem"]):
            query_type = "theory"
        elif any(kw in query_lower for kw in ["benchmark", "eval", "test", "metric"]):
            query_type = "benchmark"
    
    # Classification-based ratios
    if query_type in ["theory", "mathematical", "proof"]:
        # Theory questions: heavily prefer papers
        return 0.30
    
    elif query_type in ["benchmark", "evaluation", "comparison"]:
        # Benchmark/evaluation: prefer papers but allow some code
        return 0.35
    
    elif query_type in ["implementation", "how-to", "tutorial"]:
        # Implementation questions: code is legitimate primary source
        # Examples: "How to implement X", "Best practices for Y framework"
        return 0.55
    
    elif query_type in ["debugging", "troubleshooting", "code-specific"]:
        # Code-specific: repos/issues are the main evidence
        return 0.65
    
    else:
        # Default/unknown: use original conservative 40%
        return 0.40


def should_relax_code_ratio(brief: dict) -> tuple[bool, float]:
    """Determine if code ratio should be relaxed for this query.
    
    Args:
        brief: Briefing dict with query_type, depth, etc.
    
    Returns:
        (should_relax: bool, new_ratio: float)
    """
    query_type = brief.get("query_type") or brief.get("category")
    query = brief.get("query") or ""
    
    ratio = adaptive_code_ratio(query_type, query)
    
    # Relax if ratio > default 40%
    should_relax = ratio > 0.40
    
    return (should_relax, ratio)


def explain_code_ratio(ratio: float, query_type: str | None) -> str:
    """Generate human-readable explanation for code ratio choice.
    
    For logging/debugging.
    """
    if ratio <= 0.30:
        return f"Low code ratio ({ratio:.0%}) for {query_type or 'theory'} question - prefer academic papers"
    elif ratio <= 0.40:
        return f"Balanced code ratio ({ratio:.0%}) for {query_type or 'general'} question"
    elif ratio <= 0.55:
        return f"Elevated code ratio ({ratio:.0%}) for {query_type or 'implementation'} question - code is valid evidence"
    else:
        return f"High code ratio ({ratio:.0%}) for {query_type or 'code-specific'} question - repos are primary source"
