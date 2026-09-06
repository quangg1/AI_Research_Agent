"""Paper concept extraction for evidence-first dimension synthesis.

Extracts key technical concepts, methods, and findings from papers to create
paper-specific research dimensions instead of generic taxonomy templates.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.textutil import distinctive_terms


def extract_paper_concepts(evidence: list[dict], limit: int = 5) -> list[dict]:
    """Extract key technical concepts from top papers for dimension synthesis.
    
    Scans abstracts, titles, and snippets to identify:
    - Technical method names
    - Framework/architecture names  
    - Benchmark/dataset names
    - Key findings with metrics
    - Limitations/constraints
    
    Args:
        evidence: List of evidence dicts from scholar/search
        limit: Max number of papers to analyze
        
    Returns:
        List of concept dicts with:
        - concept_name: "FreeAL active verifier"
        - paper_id: citation ID
        - paper_title: full title
        - context: surrounding text
        - metrics: extracted numbers + conditions
        - concept_type: method/finding/limitation
    """
    concepts: list[dict] = []
    
    # Focus on high-quality sources first
    sorted_evidence = sorted(
        evidence[:limit],
        key=lambda e: (
            1 if e.get("tier") in {"peer_reviewed", "specialist_research"} else 0,
            float(e.get("credibility") or 0.5)
        ),
        reverse=True
    )
    
    for idx, ev in enumerate(sorted_evidence[:limit]):
        paper_concepts = _extract_from_paper(ev, rank=idx)
        concepts.extend(paper_concepts)
    
    # Deduplicate similar concepts
    concepts = _deduplicate_concepts(concepts)
    
    return concepts


def _extract_from_paper(ev: dict, rank: int) -> list[dict]:
    """Extract concepts from a single paper."""
    concepts = []
    
    title = ev.get("title") or ""
    abstract = ev.get("snippet") or ev.get("quote") or ""
    full_text = ev.get("full_text") or ""
    paper_id = ev.get("id") or f"source_{rank}"
    cite_id = ev.get("n") or rank + 1
    
    # Combine text for analysis
    text = f"{title}. {abstract} {full_text[:1000]}"
    
    # Extract technical method names
    method_concepts = _extract_methods(text, paper_id, cite_id, title)
    concepts.extend(method_concepts)
    
    # Extract frameworks/architectures
    framework_concepts = _extract_frameworks(text, paper_id, cite_id, title)
    concepts.extend(framework_concepts)
    
    # Extract key findings with metrics
    finding_concepts = _extract_findings(text, paper_id, cite_id, title)
    concepts.extend(finding_concepts)
    
    # Extract limitations/constraints
    limitation_concepts = _extract_limitations(text, paper_id, cite_id, title)
    concepts.extend(limitation_concepts)
    
    return concepts


# Patterns for technical concepts
METHOD_PATTERNS = [
    # Multi-word technical terms
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s+(?:mechanism|method|technique|approach|algorithm|framework|pipeline|strategy)',
    # Hyphenated methods
    r'\b([a-z]+-[a-z]+(?:-[a-z]+)?)\s+(?:generation|synthesis|training|learning|optimization)',
    # Acronyms followed by expansion
    r'\b([A-Z]{2,})\s+\([^)]+\)',
    # Agent/model names
    r'\b((?:Self|Multi|Cross|Auto|Meta)-[A-Z][a-z]+)',
]

FRAMEWORK_PATTERNS = [
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:architecture|framework|system|platform|pipeline)',
    r'\b((?:end-to-end|multi-stage|hierarchical|distributed)\s+[a-z]+(?:\s+[a-z]+)?)\s+(?:pipeline|framework|architecture)',
]

METRIC_PATTERNS = [
    # Percentage improvements
    r'(\d+(?:\.\d+)?%)\s+(?:improvement|increase|gain|accuracy|F1|precision|recall)',
    # Scores
    r'(?:accuracy|F1|score|performance)[:\s]+(\d+(?:\.\d+)?%?)',
    # Comparisons
    r'from\s+(\d+(?:\.\d+)?%?)\s+to\s+(\d+(?:\.\d+)?%?)',
    r'(\d+(?:\.\d+)?%?)\s+vs\.?\s+(\d+(?:\.\d+)?%?)',
]

LIMITATION_PATTERNS = [
    r'(limitation|constraint|challenge|bottleneck|trade-?off|drawback)[:\s]+([^.]+)',
    r'does not\s+([^.]+)',
    r'cannot\s+([^.]+)',
    r'fails?\s+(?:when|if|to)\s+([^.]+)',
]


def _extract_methods(text: str, paper_id: str, cite_id: int, title: str) -> list[dict]:
    """Extract technical method names."""
    concepts = []
    
    for pattern in METHOD_PATTERNS:
        # Case-sensitive on purpose: every METHOD_PATTERNS entry uses
        # [A-Z]/[a-z] to tell a proper-noun-looking method name apart from
        # ordinary prose. re.I collapses that distinction — [A-Z]{2,}, meant
        # to require a real acronym like "RAG", then matches any lowercase
        # word ("obtain", "materials") sitting in front of a parenthetical,
        # and [A-Z][a-z]+ (repeated) matches ordinary lowercase sentence
        # words too, producing concept names like "believe that the X".
        for match in re.finditer(pattern, text):
            method_name = match.group(1).strip()
            
            # Filter out generic terms
            if _is_generic_term(method_name):
                continue
            
            # Extract surrounding context
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = text[start:end].strip()
            
            concepts.append({
                "concept_name": method_name,
                "paper_id": paper_id,
                "cite_id": cite_id,
                "paper_title": title,
                "context": context,
                "concept_type": "method",
                "metrics": _extract_nearby_metrics(context),
            })
    
    return concepts


def _extract_frameworks(text: str, paper_id: str, cite_id: int, title: str) -> list[dict]:
    """Extract framework/architecture names."""
    concepts = []
    
    for pattern in FRAMEWORK_PATTERNS:
        # Case-sensitive for the same reason as _extract_methods above.
        for match in re.finditer(pattern, text):
            framework_name = match.group(1).strip()
            
            if _is_generic_term(framework_name):
                continue
            
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = text[start:end].strip()
            
            concepts.append({
                "concept_name": framework_name,
                "paper_id": paper_id,
                "cite_id": cite_id,
                "paper_title": title,
                "context": context,
                "concept_type": "framework",
                "metrics": _extract_nearby_metrics(context),
            })
    
    return concepts


def _extract_findings(text: str, paper_id: str, cite_id: int, title: str) -> list[dict]:
    """Extract key findings with metrics."""
    concepts = []
    
    # Look for sentences with metrics
    sentences = re.split(r'[.!?]\s+', text)
    
    for sentence in sentences:
        metrics = _extract_nearby_metrics(sentence)
        
        if not metrics:
            continue
        
        # Look for what was measured
        finding_match = re.search(
            r'(achieve[sd]?|reach(?:ed|es)?|obtain(?:ed|s)?|show[sn]?|demonstrate[sd]?|improve[sd]?)\s+([^,\.]+)',
            sentence,
            re.I
        )
        
        if finding_match:
            finding_text = finding_match.group(2).strip()
            
            concepts.append({
                "concept_name": f"Finding: {finding_text[:50]}",
                "paper_id": paper_id,
                "cite_id": cite_id,
                "paper_title": title,
                "context": sentence,
                "concept_type": "finding",
                "metrics": metrics,
            })
    
    return concepts


def _extract_limitations(text: str, paper_id: str, cite_id: int, title: str) -> list[dict]:
    """Extract limitations and constraints."""
    concepts = []
    
    for pattern in LIMITATION_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            limitation_text = match.group(0).strip()
            
            if len(limitation_text) < 20 or len(limitation_text) > 200:
                continue
            
            concepts.append({
                "concept_name": f"Limitation: {limitation_text[:50]}",
                "paper_id": paper_id,
                "cite_id": cite_id,
                "paper_title": title,
                "context": limitation_text,
                "concept_type": "limitation",
                "metrics": [],
            })
    
    return concepts


def _extract_nearby_metrics(text: str) -> list[dict]:
    """Extract metrics from text with their conditions."""
    metrics = []
    
    for pattern in METRIC_PATTERNS:
        for match in re.finditer(pattern, text):
            # Extract the numeric value(s)
            values = [g for g in match.groups() if g]
            
            # Look for benchmark/dataset names nearby
            benchmark_match = re.search(
                r'\bon\s+([A-Z][A-Za-z0-9-]+(?:\s+[A-Z][A-Za-z0-9-]+)?)',
                text[max(0, match.start() - 50):min(len(text), match.end() + 50)]
            )
            benchmark = benchmark_match.group(1) if benchmark_match else None
            
            # Look for conditions
            condition_match = re.search(
                r'(?:with|using|on|when|for|in)\s+([^,\.]{10,80})',
                text[match.start():min(len(text), match.end() + 100)]
            )
            condition = condition_match.group(1).strip() if condition_match else None
            
            metrics.append({
                "values": values,
                "benchmark": benchmark,
                "condition": condition,
                "raw_text": match.group(0),
            })
    
    return metrics


GENERIC_TERMS = {
    "data", "method", "approach", "technique", "system", "model", "analysis",
    "results", "findings", "work", "research", "study", "paper", "article",
    "performance", "evaluation", "comparison", "overview", "review", "survey",
}


def _is_generic_term(term: str) -> bool:
    """Check if term is too generic to be a useful concept."""
    term_lower = term.lower().strip()
    
    # Single generic words
    if term_lower in GENERIC_TERMS:
        return True
    
    # Very short
    if len(term_lower) < 3:
        return True
    
    # Only common words
    words = term_lower.split()
    if all(w in GENERIC_TERMS for w in words):
        return True
    
    return False


def _deduplicate_concepts(concepts: list[dict]) -> list[dict]:
    """Remove duplicate or very similar concepts."""
    if not concepts:
        return []
    
    unique: list[dict] = []
    seen_names: set[str] = set()
    
    for concept in concepts:
        name = concept["concept_name"].lower().strip()
        
        # Skip exact duplicates
        if name in seen_names:
            continue
        
        # Skip very similar names (simple heuristic)
        is_similar = False
        for seen in list(seen_names):
            # Check if one is substring of other
            if name in seen or seen in name:
                # Keep the longer, more specific one
                if len(name) > len(seen):
                    # Remove the shorter one from unique
                    unique = [c for c in unique if c["concept_name"].lower() != seen]
                    seen_names.discard(seen)
                else:
                    is_similar = True
                    break
        
        if not is_similar:
            unique.append(concept)
            seen_names.add(name)
    
    return unique
