"""
Citation relevance validation for research memos.

Prevents off-topic papers from being cited due to superficial keyword matching.
"""

from __future__ import annotations
import re
from typing import Any

# Core AI/ML/CS domains (each item is a complete, valid regex)
AI_ML_KEYWORDS = [
    r"\b(artificial intelligence|machine learning|deep learning|neural network)\b",
    r"\b(large language model|LLM|GPT|transformer|BERT)\b",
    r"\b(synthetic data|data generation|generative model)\b",
    r"\b(reinforcement learning|supervised learning)\b",
    r"\b(natural language processing|NLP|computer vision)\b",
    r"\b(model training|fine-tuning|pre-training)\b",
]

# Domains that should NOT be cited for AI/ML claims
OFF_TOPIC_DOMAINS = [
    r"\b(neural regeneration|brain injury|neurodegeneration|alzheimer)\b",
    r"\b(parkinson|stroke recovery|neurology|neuroscience|medical)\b",
    r"\b(genotype|phenotype|evolution|biology|genetics|organism)\b",
    r"\b(clinical trial|patient|hospital|disease|diagnosis)\b",
]

# Acceptable venues for AI/ML research
CS_AI_VENUES = [
    r"\b(arxiv\.org/abs/.*cs\.|arxiv\.org/html/.*cs\.)\b",
    r"\b(neurips|icml|iclr|acl|emnlp|naacl|cvpr|iccv|eccv)\b",
    r"\b(aaai|ijcai|kdd|www|sigir|recsys|asonam)\b",
    r"IEEE.*Transactions.*(Pattern Analysis|Neural Networks|AI)",
    r"Journal.*(Machine Learning|Artificial Intelligence)",
    r"ACM.*Conference.*(Learning|Intelligence|Data)",
    r"Springer.*(Lecture Notes.*Computer Science|Machine Learning)",
]


def check_citation_relevance(
    paper: dict[str, Any],
    claim_context: str,
    strict: bool = True
) -> tuple[bool, list[str]]:
    """
    Check if a paper is relevant to an AI/ML claim.
    
    Args:
        paper: Paper dict with title, abstract, url, venue
        claim_context: The claim text this paper is supposed to support
        strict: If True, apply strict domain filtering
    
    Returns:
        (is_relevant, list of issues)
    """
    issues: list[str] = []
    
    title = (paper.get("title") or "").lower()
    abstract = (paper.get("snippet") or paper.get("abstract") or "").lower()
    url = (paper.get("url") or "").lower()
    venue = (paper.get("venue") or "").lower()
    
    content = f"{title} {abstract} {venue}"
    
    # Check 1: Is it about AI/ML/CS?
    has_ai_keywords = any(
        re.search(pattern, content, re.I)
        for pattern in AI_ML_KEYWORDS
    )
    
    # Check 2: Is it from an off-topic domain?
    has_off_topic_keywords = any(
        re.search(pattern, content, re.I)
        for pattern in OFF_TOPIC_DOMAINS
    )
    
    # Check 3: Is venue appropriate?
    is_cs_venue = any(
        re.search(pattern, url + " " + venue, re.I)
        for pattern in CS_AI_VENUES
    )
    
    # Strict mode: Must have AI keywords AND not off-topic
    if strict:
        if not has_ai_keywords and has_off_topic_keywords:
            issues.append(
                f"Paper appears to be about {_detect_domain(content)}, "
                f"not AI/ML (title: {paper.get('title', 'Unknown')[:80]})"
            )
            return False, issues
    
    # Check claim-paper alignment
    claim_keywords = _extract_key_terms(claim_context)
    paper_keywords = _extract_key_terms(content)
    
    keyword_overlap = len(claim_keywords & paper_keywords)
    if keyword_overlap < 2:
        issues.append(
            f"Weak keyword overlap between claim and paper "
            f"(overlap={keyword_overlap}, claim terms={claim_keywords})"
        )
        # Don't reject yet, just flag
    
    return len(issues) == 0, issues


def _detect_domain(content: str) -> str:
    """Detect primary domain of content."""
    if re.search(r"\b(neural regeneration|brain|neuroscience)\b", content, re.I):
        return "neuroscience/medicine"
    if re.search(r"\b(genotype|phenotype|evolution|organism)\b", content, re.I):
        return "evolutionary biology"
    if re.search(r"\b(patient|clinical|medical|hospital)\b", content, re.I):
        return "clinical medicine"
    return "unknown non-CS domain"


def _extract_key_terms(text: str) -> set[str]:
    """Extract key technical terms from text."""
    # Simple approach: extract 2-3 word technical phrases
    text_lower = text.lower()
    terms = set()
    
    # Extract known technical bigrams/trigrams
    technical_patterns = [
        r"synthetic data",
        r"model collapse",
        r"differential privacy",
        r"preference optimization",
        r"multi-agent",
        r"self-correction",
        r"verification",
        r"agentic system",
        r"digital twin",  # Can be AI or medical!
        r"neural network",
        r"large language model",
    ]
    
    for pattern in technical_patterns:
        if re.search(pattern, text_lower):
            terms.add(pattern.replace(" ", "_"))
    
    return terms


def validate_citation_in_context(
    paper: dict[str, Any],
    claim: str,
    surrounding_text: str = ""
) -> dict[str, Any]:
    """
    Comprehensive validation of citation usage.
    
    Returns validation result with issues and suggestions.
    """
    is_relevant, issues = check_citation_relevance(
        paper, 
        claim + " " + surrounding_text,
        strict=True
    )
    
    return {
        "is_valid": is_relevant,
        "issues": issues,
        "paper_title": paper.get("title", "Unknown")[:100],
        "paper_domain": _detect_domain(
            f"{paper.get('title', '')} {paper.get('snippet', '')}"
        ),
        "confidence": 1.0 if is_relevant else 0.3,
    }
