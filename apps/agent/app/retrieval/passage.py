"""Passage-level retrieval within documents.

Reranks chunks/passages within a single document by similarity to a specific
claim or dimension, ensuring we select the most relevant excerpt rather than
defaulting to title pages or first chunks.
"""

from __future__ import annotations

import re
from typing import Any

from app.retrieval.embed import cosine, embed_texts


def split_into_passages(text: str, max_len: int = 800, overlap: int = 200) -> list[str]:
    """Split long text into overlapping passages for within-document retrieval."""
    if not text or len(text) <= max_len:
        return [text] if text else []
    
    # Try to split on paragraph boundaries first
    paragraphs = re.split(r'\n\n+', text)
    passages: list[str] = []
    current = ""
    
    for para in paragraphs:
        if not para.strip():
            continue
        # If adding this paragraph exceeds max_len, save current and start new
        if current and len(current) + len(para) > max_len:
            passages.append(current.strip())
            # Keep overlap from end of previous passage
            words = current.split()
            current = " ".join(words[-overlap // 5:]) + " " if len(words) > overlap // 5 else ""
        current += para + "\n\n"
    
    if current.strip():
        passages.append(current.strip())
    
    # If no paragraph breaks, fall back to sentence splitting
    if len(passages) <= 1:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        passages = []
        current = ""
        for sent in sentences:
            if current and len(current) + len(sent) > max_len:
                passages.append(current.strip())
                words = current.split()
                current = " ".join(words[-overlap // 5:]) + " " if len(words) > overlap // 5 else ""
            current += sent + " "
        if current.strip():
            passages.append(current.strip())
    
    return passages


def best_passage_for_claim(
    document_text: str,
    claim_or_dimension: str,
    *,
    patterns: list[str] | None = None,
    topic_terms: list[str] | None = None,
    max_passage_len: int = 800,
) -> str:
    """Select the best passage from a document for a specific claim/dimension.
    
    Args:
        document_text: Full text of the document
        claim_or_dimension: The claim, dimension label, or query to match
        patterns: Optional regex patterns that boost relevance
        topic_terms: Optional topic-specific terms that boost relevance
        max_passage_len: Maximum characters per passage
    
    Returns:
        The most relevant passage from the document, or empty string if no good match
    """
    if not document_text or not claim_or_dimension:
        return ""
    
    passages = split_into_passages(document_text, max_len=max_passage_len)
    if not passages:
        return ""
    
    # If only one passage, return it unless it's clearly junk
    if len(passages) == 1:
        if _is_title_page_or_chrome(passages[0]):
            return ""
        return passages[0]
    
    # Score each passage
    patterns = patterns or []
    topic_terms = topic_terms or []
    query_vec = embed_texts([claim_or_dimension])[0]
    passage_vecs = embed_texts(passages)
    
    scored: list[tuple[float, str]] = []
    for i, passage in enumerate(passages):
        # Allow title pages in scoring, but penalize them
        is_title = _is_title_page_or_chrome(passage)
        
        # Semantic similarity
        sem_score = cosine(query_vec, passage_vecs[i])
        
        # Pattern matching boost
        pattern_hits = sum(1 for p in patterns if re.search(p, passage, re.I))
        
        # Topic term boost
        lower_passage = passage.lower()
        term_hits = sum(1 for t in topic_terms if t.lower() in lower_passage)
        
        # Prefer passages that are not at the very start (often title/abstract)
        position_penalty = 0.15 if i == 0 and len(passages) > 2 else 0.0
        
        # Heavy penalty for title pages
        title_penalty = 0.40 if is_title else 0.0
        
        # Combine scores
        score = (
            0.60 * sem_score
            + 0.20 * min(1.0, pattern_hits / max(1, len(patterns)))
            + 0.15 * min(1.0, term_hits / max(1, len(topic_terms)))
            - position_penalty
            - title_penalty
        )
        
        scored.append((score, passage))
    
    if not scored:
        # Shouldn't happen, but return first non-junk passage as fallback
        for passage in passages:
            if not _is_title_page_or_chrome(passage):
                return passage
        return passages[0] if passages else ""
    
    # Return highest scoring passage
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _is_title_page_or_chrome(text: str) -> bool:
    """True if text appears to be a title page, keyword list, or navigation chrome."""
    if not text or len(text) < 20:
        return True
    
    # Check for common title page patterns
    lower_text = text.lower()
    
    # Very short with mostly keywords (no verbs)
    if len(text) < 200:
        words = text.split()
        # Check if it's mostly a keyword list (few verbs)
        verb_indicators = ['is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 
                           'do', 'does', 'did', 'can', 'could', 'will', 'would', 'should',
                           'measure', 'show', 'find', 'report', 'achieve', 'improve']
        verb_count = sum(1 for w in words if w.lower() in verb_indicators)
        if len(words) > 5 and verb_count == 0:
            return True
    
    # Check for title page indicators
    title_indicators = [
        'abstract', 'keywords:', 'key words:', 'published:', 'preprint',
        'authors:', 'affiliation:', 'corresponding author', 'doi:',
        'arxiv:', 'submitted to', 'accepted to', 'conference:',
    ]
    indicator_count = sum(1 for ind in title_indicators if ind in lower_text)
    if indicator_count >= 2 and len(text) < 500:
        return True
    
    # Check for navigation chrome patterns (common in docs sites)
    nav_patterns = [
        r'home\s+documentation\s+api',
        r'getting started\s+tutorial\s+api reference',
        r'docs\s+community\s+blog',
        r'search\s+navigation\s+contents',
    ]
    if any(re.search(p, lower_text, re.I) for p in nav_patterns):
        return True
    
    # Check for byline/metadata dumps
    if len(text) < 300 and text.count('\n') > len(text) / 30:
        # Many short lines = likely metadata
        return True
    
    return False


def select_best_excerpts_per_dimension(
    dimensions: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rerank evidence passages for each dimension based on dimension-specific relevance.
    
    For each dimension, re-extract the best passage from each evidence item's full text,
    scored by similarity to that dimension's label and patterns.
    
    Args:
        dimensions: List of dimension dicts with id, label, patterns, topic_terms
        evidence: List of evidence dicts with full_text, title, snippet, etc.
    
    Returns:
        Updated dimensions with evidence items having dimension-specific 'selected_passage'
    """
    updated_dims: list[dict[str, Any]] = []
    
    for dim in dimensions:
        dim_label = dim.get("label") or dim.get("id") or ""
        patterns = [p for p in (dim.get("patterns") or []) if p]
        topic_terms = [t for t in (dim.get("topic_terms") or []) if t]
        
        items = dim.get("items") or []
        updated_items: list[dict[str, Any]] = []
        
        for ev in items:
            # Build full text for passage selection
            full_text = (
                f"{ev.get('full_text') or ''} "
                f"{ev.get('quote') or ''} "
                f"{ev.get('snippet') or ''}"
            ).strip()
            
            if not full_text:
                # No text to work with, keep as-is
                updated_items.append(ev)
                continue
            
            # Select best passage for THIS dimension
            best_passage = best_passage_for_claim(
                full_text,
                dim_label,
                patterns=patterns,
                topic_terms=topic_terms,
            )
            
            # Update evidence item with dimension-specific passage
            updated_ev = dict(ev)
            if best_passage:
                updated_ev["selected_passage"] = best_passage
                # Also update quote if it's better than existing
                if len(best_passage) > len(ev.get("quote") or ""):
                    updated_ev["quote"] = best_passage
            updated_items.append(updated_ev)
        
        updated_dim = dict(dim)
        updated_dim["items"] = updated_items
        updated_dims.append(updated_dim)
    
    return updated_dims
