"""Centralized threshold configuration for Kiln agent quality controls.

All quality-related thresholds and magic numbers in ONE place.
Calibrated from multi-month audit data (trust_bench_e2e hallucination rates).

Philosophy:
- Production system requires consistent, calibrated thresholds
- Scattered magic numbers → whack-a-mole bugs and dead zones
- Single source of truth → easier to reason about and adjust

Categories:
- Coverage: Retrieval quality and depth thresholds
- Quality: Memo quality and regeneration triggers
- Structure: Structural validation rules
- Retrieval: Domain balancing and source diversity
- Budget: Iteration and tool call limits
"""

from __future__ import annotations


class CoverageThresholds:
    """Coverage and depth thresholds for retrieval quality assessment."""
    
    # Must-answer coverage percentage thresholds
    MUST_COVERAGE_EXCELLENT = 75  # Early stop, exceptional quality
    MUST_COVERAGE_GOOD = 65       # Standard quality, allow regeneration
    MUST_COVERAGE_FAIR = 50       # Minimum acceptable (with gaps disclosure)
    MUST_COVERAGE_POOR = 50       # Below this = retrieval issue, needs more research
    
    # Stagnation detection (builder.py after_critic)
    COVERAGE_STAGNATION_THRESHOLD_PCT = 2  # Consecutive diffs < 2% = stagnant
    
    # Depth score thresholds (0-100 scale)
    DEPTH_SCORE_EXCELLENT = 80
    DEPTH_SCORE_GOOD = 60
    DEPTH_SCORE_FAIR = 40
    
    # Evidence items per dimension (minimum for quality)
    MIN_EVIDENCE_ITEMS_PER_DIM = 3
    IDEAL_EVIDENCE_ITEMS_PER_DIM = 5
    
    @classmethod
    def coverage_tier(cls, must_pct: float, depth_score: float = 0) -> str:
        """Classify coverage quality into tier."""
        if must_pct >= cls.MUST_COVERAGE_EXCELLENT and depth_score >= cls.DEPTH_SCORE_EXCELLENT:
            return "excellent"
        elif must_pct >= cls.MUST_COVERAGE_GOOD:
            return "good"
        elif must_pct >= cls.MUST_COVERAGE_FAIR:
            return "fair"
        else:
            return "poor"


class QualityThresholds:
    """Memo quality and regeneration triggers."""
    
    # Hallucination rate (from trust_bench_e2e audits)
    MAX_HALLUCINATION_RATE = 0.20  # 20% is red line (based on empirical data)
    TARGET_HALLUCINATION_RATE = 0.10  # 10% is target
    
    # Citation stacking (sentences citing 3+ distinct sources)
    MAX_CITATION_STACKING_PER_1000_WORDS = 3.0
    
    # Source saturation (unique sources cited)
    MIN_UNIQUE_SOURCES = 4
    IDEAL_UNIQUE_SOURCES = 8
    
    # Regeneration limits
    MAX_QUALITY_REGENERATIONS = 2  # Prevent infinite rewrite loops
    
    # Word count targets (adaptive depth multipliers in adaptive_depth.py)
    BASE_WORDS_PER_EVIDENCE = 80  # Sustainable without hallucination
    
    # Minimum word counts (adaptive bounds)
    MIN_MEMO_WORDS = 800
    MAX_MEMO_WORDS = 7000


class StructureThresholds:
    """Structural validation rules (Tier-C programmatic checks)."""
    
    # Per-dimension subsections (comparison questions)
    MIN_SUBSECTIONS_DETAILED_ANALYSIS = 2
    
    # Worked example compositing
    MAX_SOURCES_WITHOUT_COMPOSITE_LABEL = 1  # 2+ sources requires label
    
    # Quantitative findings
    MIN_QUANTITATIVE_ROWS = 1  # If section exists, at least 1 data row
    
    # Citation stacking (same as quality threshold)
    MAX_CITATION_STACKING_PER_1000_WORDS = QualityThresholds.MAX_CITATION_STACKING_PER_1000_WORDS


class RetrievalThresholds:
    """Retrieval and domain balancing thresholds."""
    
    # Domain balancing (coding skew prevention)
    MAX_CODE_RATIO = 0.40  # Max 40% code-related papers
    IDEAL_CODE_RATIO = 0.30  # Target 30%
    
    # Source diversity (unique domains)
    MIN_UNIQUE_DOMAINS = 3
    IDEAL_UNIQUE_DOMAINS = 5
    
    # Specialist research triggers
    MIN_PRIMARY_SOURCES = 2  # For implementation questions
    MIN_PEER_REVIEWED_SOURCES = 3  # For theory questions
    
    # Evidence items per query
    MIN_EVIDENCE_ITEMS_TOTAL = 5
    IDEAL_EVIDENCE_ITEMS_TOTAL = 15
    MAX_EVIDENCE_ITEMS_TOTAL = 40  # Cap to prevent overwhelming writer


class BudgetThresholds:
    """Iteration and tool call budget limits."""
    
    # Iteration limits
    MAX_ITERATIONS = 6
    TYPICAL_ITERATIONS = 4
    FAST_STOP_ITERATIONS = 3  # For simple definitional questions
    
    # Tool calls per iteration
    TYPICAL_TOOLS_PER_ITERATION = 10
    MAX_TOOLS_PER_ITERATION = 25
    
    # Early stop on excellence (builder.py)
    EARLY_STOP_COVERAGE_THRESHOLD = CoverageThresholds.MUST_COVERAGE_EXCELLENT
    
    # Cost estimation (rough)
    COST_PER_ITERATION_USD = 0.50  # Approximate, varies by depth


class AdaptiveDepthMultipliers:
    """Quality multipliers for adaptive depth calculation."""
    
    EXCELLENT = 1.3  # >= 75% coverage, can write deeper synthesis
    GOOD = 1.0       # 65-74% coverage, standard depth
    FAIR = 0.8       # 50-64% coverage, write shorter, admit gaps
    POOR = 0.6       # < 50% coverage, minimal synthesis
    
    @classmethod
    def get_multiplier(cls, coverage_tier: str) -> float:
        """Get multiplier for coverage tier."""
        multipliers = {
            "excellent": cls.EXCELLENT,
            "good": cls.GOOD,
            "fair": cls.FAIR,
            "poor": cls.POOR
        }
        return multipliers.get(coverage_tier, cls.GOOD)


# ============================================================================
# Derived thresholds (computed from base thresholds)
# ============================================================================

def graceful_degradation_threshold() -> int:
    """Word count below which to degrade from 'deep' to 'standard'."""
    return 1500  # Fixed for now, could be adaptive


def retrieval_issue_vs_generation_issue(must_pct: float) -> str:
    """Determine if issue is retrieval (needs more search) or generation (rewrite).
    
    Logic:
    - < 65%: Retrieval issue (need more evidence)
    - >= 65%: Generation issue (evidence sufficient, writer problem)
    """
    if must_pct < CoverageThresholds.MUST_COVERAGE_GOOD:
        return "retrieval"
    else:
        return "generation"


# ============================================================================
# Validation and consistency checks
# ============================================================================

def validate_thresholds():
    """Check threshold consistency (call at module load or in tests)."""
    issues = []
    
    # Coverage thresholds must be ordered
    if not (CoverageThresholds.MUST_COVERAGE_EXCELLENT > 
            CoverageThresholds.MUST_COVERAGE_GOOD > 
            CoverageThresholds.MUST_COVERAGE_FAIR):
        issues.append("Coverage thresholds not properly ordered")
    
    # Regeneration limit should be reasonable (2-5)
    if not (1 <= QualityThresholds.MAX_QUALITY_REGENERATIONS <= 5):
        issues.append("MAX_QUALITY_REGENERATIONS out of reasonable range")
    
    # Code ratio should be < 0.5
    if RetrievalThresholds.MAX_CODE_RATIO >= 0.5:
        issues.append("MAX_CODE_RATIO should be < 0.5 to prevent coding skew")
    
    # Citation stacking thresholds should match
    if (StructureThresholds.MAX_CITATION_STACKING_PER_1000_WORDS != 
        QualityThresholds.MAX_CITATION_STACKING_PER_1000_WORDS):
        issues.append("Citation stacking thresholds inconsistent between Structure and Quality")
    
    if issues:
        raise ValueError(f"Threshold validation failed: {issues}")


# Run validation at import time
validate_thresholds()


# ============================================================================
# Usage examples and documentation
# ============================================================================

def get_all_thresholds() -> dict:
    """Return all thresholds as dict (for logging/debugging)."""
    return {
        "coverage": {
            "must_excellent": CoverageThresholds.MUST_COVERAGE_EXCELLENT,
            "must_good": CoverageThresholds.MUST_COVERAGE_GOOD,
            "must_fair": CoverageThresholds.MUST_COVERAGE_FAIR,
            "must_poor": CoverageThresholds.MUST_COVERAGE_POOR,
            "stagnation_threshold_pct": CoverageThresholds.COVERAGE_STAGNATION_THRESHOLD_PCT,
            "depth_excellent": CoverageThresholds.DEPTH_SCORE_EXCELLENT,
            "depth_good": CoverageThresholds.DEPTH_SCORE_GOOD,
            "depth_fair": CoverageThresholds.DEPTH_SCORE_FAIR,
        },
        "quality": {
            "max_hallucination_rate": QualityThresholds.MAX_HALLUCINATION_RATE,
            "target_hallucination_rate": QualityThresholds.TARGET_HALLUCINATION_RATE,
            "max_citation_stacking_per_1000": QualityThresholds.MAX_CITATION_STACKING_PER_1000_WORDS,
            "min_unique_sources": QualityThresholds.MIN_UNIQUE_SOURCES,
            "max_quality_regenerations": QualityThresholds.MAX_QUALITY_REGENERATIONS,
            "base_words_per_evidence": QualityThresholds.BASE_WORDS_PER_EVIDENCE,
            "min_memo_words": QualityThresholds.MIN_MEMO_WORDS,
            "max_memo_words": QualityThresholds.MAX_MEMO_WORDS,
        },
        "structure": {
            "min_subsections_detailed_analysis": StructureThresholds.MIN_SUBSECTIONS_DETAILED_ANALYSIS,
            "max_sources_without_composite": StructureThresholds.MAX_SOURCES_WITHOUT_COMPOSITE_LABEL,
            "min_quantitative_rows": StructureThresholds.MIN_QUANTITATIVE_ROWS,
        },
        "retrieval": {
            "max_code_ratio": RetrievalThresholds.MAX_CODE_RATIO,
            "ideal_code_ratio": RetrievalThresholds.IDEAL_CODE_RATIO,
            "min_unique_domains": RetrievalThresholds.MIN_UNIQUE_DOMAINS,
            "min_primary_sources": RetrievalThresholds.MIN_PRIMARY_SOURCES,
            "min_peer_reviewed": RetrievalThresholds.MIN_PEER_REVIEWED_SOURCES,
        },
        "budget": {
            "max_iterations": BudgetThresholds.MAX_ITERATIONS,
            "typical_iterations": BudgetThresholds.TYPICAL_ITERATIONS,
            "fast_stop_iterations": BudgetThresholds.FAST_STOP_ITERATIONS,
            "early_stop_coverage": BudgetThresholds.EARLY_STOP_COVERAGE_THRESHOLD,
        }
    }
