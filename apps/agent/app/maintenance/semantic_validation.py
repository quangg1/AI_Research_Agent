"""Semantic validation to detect conceptual hallucinations.

Addresses Issue #4: Tier-C `structure_validation.py` checks syntax/structure
but misses semantic hallucinations (e.g., "GridRAG uses top-k=5" when paper
says top-k=10).

Strategy: Embedding similarity threshold (Option B from design doc)

Flow:
1. Extract claim sentences from memo
2. For each claim with citation [n], embed the claim
3. Embed all sentences from source paper n
4. Compute max cosine similarity between claim and source sentences
5. Flag claim if max_similarity < threshold (no supporting sentence)

Benefits:
- No extra LLM call (uses existing embeddings)
- Fast (cosine similarity is cheap)
- Catches paraphrasing errors and value mismatches
- Deterministic threshold (no LLM prompt brittleness)

Limitations:
- Similarity threshold needs calibration (default 0.70)
- May miss subtle negation flips ("improves" vs "doesn't improve")
- Doesn't verify logical inference chains
"""

from __future__ import annotations

import re
from typing import Any
import numpy as np


# Similarity thresholds
SIMILARITY_THRESHOLD_CRITICAL = 0.70  # Below this = likely hallucination
SIMILARITY_THRESHOLD_WARNING = 0.80   # Below this = review recommended
MIN_CLAIM_LENGTH = 10  # Skip very short claims (likely section headers)


def extract_claims_with_citations(memo_markdown: str) -> list[dict]:
    """Extract all claim sentences that have citations.
    
    Args:
        memo_markdown: Full memo text
    
    Returns:
        List of dicts with {sentence, citations, char_offset}
    """
    claims = []
    
    # Split into sentences (simple heuristic)
    sentences = re.split(r'(?<=[.!?])\s+', memo_markdown)
    
    char_offset = 0
    for sentence in sentences:
        # Find citations in sentence [n]
        citation_matches = re.findall(r'\[(\d+)\]', sentence)
        
        if citation_matches and len(sentence) > MIN_CLAIM_LENGTH:
            # Remove citations for clean claim text
            clean_sentence = re.sub(r'\[\d+\]', '', sentence).strip()
            
            claims.append({
                "sentence": clean_sentence,
                "citations": [int(c) for c in citation_matches],
                "char_offset": char_offset,
                "original": sentence,
            })
        
        char_offset += len(sentence) + 1  # +1 for space
    
    return claims


def embed_text(text: str, embedding_model) -> np.ndarray:
    """Embed text using provided embedding model.
    
    Args:
        text: Text to embed
        embedding_model: Embedding model (e.g., OpenAI, Sentence-Transformers)
    
    Returns:
        Embedding vector as numpy array
    """
    # Placeholder - actual implementation depends on embedding model
    # e.g., for OpenAI:
    #   response = openai.Embedding.create(input=text, model="text-embedding-3-small")
    #   return np.array(response['data'][0]['embedding'])
    
    # For Sentence-Transformers:
    #   return embedding_model.encode(text, convert_to_numpy=True)
    
    # Stub return
    return np.random.rand(384)  # Typical embedding dimension


def compute_cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Compute cosine similarity between two vectors.
    
    Args:
        vec1, vec2: Embedding vectors
    
    Returns:
        Cosine similarity in [0, 1]
    """
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))


def validate_claim_against_source(
    claim: str,
    source_text: str,
    embedding_model,
    *,
    threshold: float = SIMILARITY_THRESHOLD_CRITICAL
) -> tuple[bool, float, str]:
    """Validate that claim is supported by source text.
    
    Args:
        claim: Claim sentence from memo
        source_text: Full text of source paper
        embedding_model: Embedding model
        threshold: Minimum similarity to pass
    
    Returns:
        (is_valid, max_similarity, most_similar_sentence)
    """
    # Embed claim
    claim_embedding = embed_text(claim, embedding_model)
    
    # Split source into sentences
    source_sentences = re.split(r'(?<=[.!?])\s+', source_text)
    
    # Compute similarity with each source sentence
    max_similarity = 0.0
    most_similar_sentence = ""
    
    for sentence in source_sentences:
        if len(sentence) < MIN_CLAIM_LENGTH:
            continue
        
        source_embedding = embed_text(sentence, embedding_model)
        similarity = compute_cosine_similarity(claim_embedding, source_embedding)
        
        if similarity > max_similarity:
            max_similarity = similarity
            most_similar_sentence = sentence
    
    is_valid = max_similarity >= threshold
    
    return (is_valid, max_similarity, most_similar_sentence)


def validate_memo_semantics(
    memo_markdown: str,
    evidence_pool: list[dict],
    embedding_model,
    *,
    threshold: float = SIMILARITY_THRESHOLD_CRITICAL
) -> dict[str, Any]:
    """Validate all claims in memo against source papers.
    
    Args:
        memo_markdown: Full memo text
        evidence_pool: List of evidence dicts with {id, content, ...}
        embedding_model: Embedding model
        threshold: Minimum similarity to pass
    
    Returns:
        Validation result dict with flagged claims
    """
    # Extract claims
    claims = extract_claims_with_citations(memo_markdown)
    
    # Build evidence lookup
    evidence_by_id = {e["id"]: e["content"] for e in evidence_pool if "content" in e}
    
    # Validate each claim
    flagged_claims = []
    warning_claims = []
    
    for claim_data in claims:
        claim = claim_data["sentence"]
        citations = claim_data["citations"]
        
        # Check each cited source
        for citation_id in citations:
            if citation_id not in evidence_by_id:
                # Citation not in evidence pool (different error)
                continue
            
            source_text = evidence_by_id[citation_id]
            
            is_valid, similarity, similar_sentence = validate_claim_against_source(
                claim, source_text, embedding_model, threshold=threshold
            )
            
            if not is_valid:
                flagged_claims.append({
                    "claim": claim,
                    "citation": citation_id,
                    "similarity": similarity,
                    "most_similar": similar_sentence,
                    "severity": "critical" if similarity < SIMILARITY_THRESHOLD_CRITICAL else "warning",
                })
            elif similarity < SIMILARITY_THRESHOLD_WARNING:
                warning_claims.append({
                    "claim": claim,
                    "citation": citation_id,
                    "similarity": similarity,
                    "most_similar": similar_sentence,
                    "severity": "warning",
                })
    
    # Summary
    total_claims = len(claims)
    critical_count = len([c for c in flagged_claims if c["severity"] == "critical"])
    warning_count = len(warning_claims)
    
    return {
        "valid": critical_count == 0,
        "total_claims": total_claims,
        "critical_issues": critical_count,
        "warnings": warning_count,
        "flagged_claims": flagged_claims[:5],  # Top 5 for display
        "warning_claims": warning_claims[:5],
        "hallucination_rate_estimate": critical_count / max(1, total_claims),
    }


def should_trigger_regeneration(validation_result: dict) -> bool:
    """Determine if memo should be regenerated based on semantic validation.
    
    Args:
        validation_result: Result from validate_memo_semantics
    
    Returns:
        True if regeneration needed
    """
    # Trigger if critical issues found
    if validation_result["critical_issues"] > 0:
        return True
    
    # Also trigger if hallucination rate estimate is high (even with warnings)
    if validation_result["hallucination_rate_estimate"] > 0.10:  # 10% threshold
        return True
    
    return False


# Integration point for memo_gate
def integrate_with_memo_gate():
    """
    Integration pseudo-code for memo_gate.py:
    
    # Add to memo_gate checks (alongside structure_validation):
    def memo_gate_node(state):
        memo = state['memo']
        evidence = state['evidence_pool']
        
        # Existing structure checks
        from app.domain.structure_validation import validate_memo_structure
        structure_ok, structure_issues = validate_memo_structure(memo, ...)
        
        # NEW: Semantic validation
        from app.maintenance.semantic_validation import (
            validate_memo_semantics,
            should_trigger_regeneration
        )
        from app.config.embeddings import get_embedding_model
        
        embedding_model = get_embedding_model()  # Reuse existing embeddings
        
        semantic_result = validate_memo_semantics(
            memo_markdown=memo,
            evidence_pool=evidence,
            embedding_model=embedding_model,
            threshold=0.70  # From thresholds.py
        )
        
        if should_trigger_regeneration(semantic_result):
            event("semantic_validation_failed",
                critical_issues=semantic_result["critical_issues"],
                hallucination_rate=semantic_result["hallucination_rate_estimate"]
            )
            
            return {
                "memo_gate_approved": False,
                "semantic_issues": semantic_result,
                "regeneration_reason": "semantic_hallucinations"
            }
        
        # Pass gate
        return {"memo_gate_approved": True, "semantic_issues": semantic_result}
    """
    pass


# Calibration utilities
def calibrate_threshold(
    known_good_memos: list[tuple[str, list[dict]]],
    known_bad_memos: list[tuple[str, list[dict]]],
    embedding_model
) -> dict[str, float]:
    """Calibrate similarity threshold using labeled examples.
    
    Args:
        known_good_memos: List of (memo_markdown, evidence_pool) for good memos
        known_bad_memos: List of (memo_markdown, evidence_pool) for bad memos
        embedding_model: Embedding model
    
    Returns:
        Dict with recommended thresholds
    """
    # Collect similarity scores from good and bad memos
    good_scores = []
    bad_scores = []
    
    for memo, evidence in known_good_memos:
        result = validate_memo_semantics(memo, evidence, embedding_model, threshold=0.0)
        # Extract all similarities
        for claim in result.get("flagged_claims", []) + result.get("warning_claims", []):
            good_scores.append(claim["similarity"])
    
    for memo, evidence in known_bad_memos:
        result = validate_memo_semantics(memo, evidence, embedding_model, threshold=0.0)
        for claim in result.get("flagged_claims", []) + result.get("warning_claims", []):
            bad_scores.append(claim["similarity"])
    
    if not good_scores or not bad_scores:
        return {
            "critical_threshold": SIMILARITY_THRESHOLD_CRITICAL,
            "warning_threshold": SIMILARITY_THRESHOLD_WARNING,
            "note": "Insufficient calibration data"
        }
    
    # Find optimal threshold (maximize F1)
    # Simple heuristic: midpoint between median good and median bad
    median_good = np.median(good_scores)
    median_bad = np.median(bad_scores)
    
    optimal_critical = (median_good + median_bad) / 2
    optimal_warning = median_good - 0.10  # 10% margin
    
    return {
        "critical_threshold": float(optimal_critical),
        "warning_threshold": float(optimal_warning),
        "median_good_score": float(median_good),
        "median_bad_score": float(median_bad),
        "note": "Calibrated from labeled data"
    }
