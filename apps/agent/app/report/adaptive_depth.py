"""Adaptive depth calculation based on actual evidence quality.

Replaces fixed word targets (5500 for deep) with evidence-driven targets.
Prevents writer pressure to hallucinate/composite when evidence is thin.
"""

from __future__ import annotations

from typing import Any

from app.config.thresholds import (
    AdaptiveDepthMultipliers,
    CoverageThresholds,
    QualityThresholds,
    graceful_degradation_threshold,
)


def calculate_adaptive_target(
    dossier: list[dict],
    coverage: dict,
    *,
    base_words_per_evidence: int = QualityThresholds.BASE_WORDS_PER_EVIDENCE,
    quality_multipliers: dict[str, float] | None = None
) -> dict[str, Any]:
    """Calculate adaptive word target based on actual evidence quality.
    
    Core principle: ~80 words per evidence item is sustainable without hallucination.
    Adjust by coverage quality (high coverage → can write deeper, low → write shorter).
    
    Args:
        dossier: Dimension-grouped evidence from coverage.py
        coverage: Coverage dict with depth_score, must_answer, etc.
        base_words_per_evidence: Base words per evidence item (default 80)
        quality_multipliers: Optional custom multipliers by coverage tier
    
    Returns:
        dict with:
            - total_words: Adaptive target for full memo
            - per_dimension_words: Target per dimension subsection
            - rationale: Explanation of calculation
            - coverage_tier: excellent/good/fair/poor
            - evidence_count: Total evidence items
            - dimension_count: Number of dimensions
    """
    # Count total evidence items across all dimensions
    total_evidence_items = sum(len(dim.get("items") or []) for dim in dossier)
    
    # Count dimensions with actual evidence
    dimension_count = len([d for d in dossier if (d.get("items") or [])])
    
    # Extract coverage metrics
    depth_score_dict = coverage.get("depth_score") or {}
    must_answer_dict = depth_score_dict.get("must_answer") or {}
    must_pct = must_answer_dict.get("pct") or 0
    overall_depth_score = depth_score_dict.get("score") or 0
    
    # Determine coverage tier and multiplier
    if quality_multipliers is None:
        quality_multipliers = {
            "excellent": AdaptiveDepthMultipliers.EXCELLENT,
            "good": AdaptiveDepthMultipliers.GOOD,
            "fair": AdaptiveDepthMultipliers.FAIR,
            "poor": AdaptiveDepthMultipliers.POOR
        }
    
    if must_pct >= CoverageThresholds.MUST_COVERAGE_EXCELLENT and overall_depth_score >= CoverageThresholds.DEPTH_SCORE_EXCELLENT:
        coverage_tier = "excellent"
    elif must_pct >= CoverageThresholds.MUST_COVERAGE_GOOD:
        coverage_tier = "good"
    elif must_pct >= CoverageThresholds.MUST_COVERAGE_FAIR:
        coverage_tier = "fair"
    else:
        coverage_tier = "poor"
    
    multiplier = quality_multipliers[coverage_tier]
    
    # Base calculation: evidence_items × words_per_item × quality_multiplier
    base_target = total_evidence_items * base_words_per_evidence
    adaptive_target = int(base_target * multiplier)
    
    # Sanity bounds (from centralized config)
    adaptive_target = max(QualityThresholds.MIN_MEMO_WORDS, min(adaptive_target, QualityThresholds.MAX_MEMO_WORDS))
    
    # Per-dimension target (for subsection guidance)
    if dimension_count > 0:
        per_dimension_words = adaptive_target // dimension_count
    else:
        per_dimension_words = 200  # Fallback for edge cases
    
    # Rationale string for transparency
    rationale = (
        f"{total_evidence_items} evidence items × {base_words_per_evidence} words/item × "
        f"{multiplier} ({coverage_tier}: {must_pct:.0f}% coverage) = {adaptive_target} words"
    )
    
    return {
        "total_words": adaptive_target,
        "per_dimension_words": per_dimension_words,
        "rationale": rationale,
        "coverage_tier": coverage_tier,
        "evidence_count": total_evidence_items,
        "dimension_count": dimension_count,
        "coverage_pct": must_pct,
        "depth_score": overall_depth_score,
        "multiplier": multiplier
    }


def should_use_graceful_degradation(
    adaptive_target_result: dict,
    *,
    degradation_threshold_words: int = graceful_degradation_threshold()
) -> tuple[bool, str]:
    """Determine if memo should gracefully degrade from 'deep' to 'standard'.
    
    Args:
        adaptive_target_result: Result from calculate_adaptive_target
        degradation_threshold_words: Word count below which to degrade (default 1500)
    
    Returns:
        (should_degrade, suggested_depth)
    """
    total_words = adaptive_target_result["total_words"]
    coverage_tier = adaptive_target_result["coverage_tier"]
    evidence_count = adaptive_target_result["evidence_count"]
    
    # Degrade if:
    # 1. Target is very low (<= 1500 words), OR
    # 2. Coverage is poor AND evidence is sparse
    if total_words <= degradation_threshold_words:
        return (True, "standard")
    elif coverage_tier == "poor" and evidence_count < 10:
        return (True, "standard")
    else:
        return (False, "deep")


def format_writer_guidance(adaptive_target_result: dict) -> str:
    """Format adaptive target as writer instruction string.
    
    Args:
        adaptive_target_result: Result from calculate_adaptive_target
    
    Returns:
        Formatted instruction string for writer prompt
    """
    total = adaptive_target_result["total_words"]
    per_dim = adaptive_target_result["per_dimension_words"]
    tier = adaptive_target_result["coverage_tier"]
    evidence_count = adaptive_target_result["evidence_count"]
    
    guidance = f"""Target length: {total} words (adaptive, based on {evidence_count} evidence items).
Per-dimension target: {per_dim} words per subsection.
Coverage tier: {tier}.

CRITICAL ADAPTIVE RULES:
- If a dimension has only 2-3 evidence items, write 120-180 words, NOT 250-450.
- If a dimension has 5-7 evidence items, write 200-300 words.
- If a dimension has 8+ evidence items, write 300-450 words.
- DO NOT pad with speculation to reach fixed targets.
- Better to write a concise, well-grounded {total}-word memo than a padded {total*1.5:.0f}-word memo with hallucinations.
- If evidence is insufficient, explicitly state "Limited evidence available for this dimension" rather than composite/speculate."""
    
    return guidance


def adaptive_depth_summary(adaptive_target_result: dict) -> dict:
    """Create summary dict for logging/metrics.
    
    Returns dict suitable for inclusion in report.metrics.
    """
    return {
        "adaptive_depth_enabled": True,
        "adaptive_target_words": adaptive_target_result["total_words"],
        "adaptive_per_dimension_words": adaptive_target_result["per_dimension_words"],
        "coverage_tier": adaptive_target_result["coverage_tier"],
        "evidence_count": adaptive_target_result["evidence_count"],
        "dimension_count": adaptive_target_result["dimension_count"],
        "rationale": adaptive_target_result["rationale"]
    }
