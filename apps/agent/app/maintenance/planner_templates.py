"""Planning templates to replace post-hoc LLM self-grading.

Addresses Issue #5: Planner uses LLM self-grading ('plan_quality_score')
which is unreliable. Plans can fail but still get high self-assessed scores.

Strategy: Template-driven planning (Option A from design doc)

Flow:
1. Classify query → template
2. Populate template with query-specific dimensions
3. Validate completeness (all dimensions filled)
4. Return structured plan (no self-grading)

Benefits:
- No hallucinated quality scores
- Deterministic validation (all sections filled = complete)
- Faster (no extra LLM call for grading)
- Easier to test
"""

from __future__ import annotations

import re
from typing import Any, Literal


# Query classification patterns
PATTERNS = {
    "comparison": [
        r"\bcompare\b",
        r"\bvs\.?\b",
        r"\bversus\b",
        r"\bdifference between\b",
        r"\bwhich is better\b",
        r"\badvantages? and disadvantages?\b",
    ],
    "implementation": [
        r"\bhow to (implement|build|create|set up)\b",
        r"\bstep-by-step\b",
        r"\bguide to\b",
        r"\btutorial\b",
        r"\bcode example\b",
    ],
    "survey": [
        r"\bsurvey of\b",
        r"\boverview of\b",
        r"\bstate of the art\b",
        r"\brecent advances\b",
        r"\bliterature review\b",
    ],
    "theory": [
        r"\bwhy does\b",
        r"\btheory behind\b",
        r"\bprinciples of\b",
        r"\bfoundations? of\b",
        r"\bmathematical basis\b",
    ],
    "benchmark": [
        r"\bperformance of\b",
        r"\bbenchmark\b",
        r"\bmetrics\b",
        r"\baccuracy\b",
        r"\blatency\b",
        r"\bthroughput\b",
    ],
}


# Planning templates
TEMPLATES = {
    "comparison": {
        "must_answer": [
            "What are the key architectural differences between the approaches?",
            "What are the performance trade-offs (accuracy, latency, resource usage)?",
            "What are the implementation complexity differences?",
            "In what scenarios is each approach preferred?",
        ],
        "should_answer": [
            "What are representative worked examples of each approach?",
            "What benchmarks compare the approaches head-to-head?",
        ],
        "could_answer": [
            "What are the historical origins of each approach?",
            "What are emerging variations or hybrids?",
        ],
        "retrieval_focus": [
            "Papers that directly compare the approaches",
            "Benchmark papers with quantitative comparisons",
            "Survey papers discussing trade-offs",
        ],
    },
    "implementation": {
        "must_answer": [
            "What is the high-level architecture?",
            "What are the core components and their interactions?",
            "What are the implementation steps?",
            "What are the common pitfalls and solutions?",
        ],
        "should_answer": [
            "What is a complete worked example with code?",
            "What are the configuration options and their effects?",
        ],
        "could_answer": [
            "What are alternative implementation approaches?",
            "What are performance optimization techniques?",
        ],
        "retrieval_focus": [
            "Papers with implementation details and code",
            "Tutorial papers and documentation",
            "Papers discussing deployment experiences",
        ],
    },
    "survey": {
        "must_answer": [
            "What are the main categories of approaches?",
            "What is the historical evolution of the field?",
            "What are the current state-of-the-art methods?",
            "What are the open problems and future directions?",
        ],
        "should_answer": [
            "What are the key benchmark results across methods?",
            "What are the theoretical foundations?",
        ],
        "could_answer": [
            "What are the connections to related fields?",
            "What are the practical adoption patterns?",
        ],
        "retrieval_focus": [
            "Survey papers and literature reviews",
            "Recent papers on state-of-the-art methods",
            "Position papers on open problems",
        ],
    },
    "theory": {
        "must_answer": [
            "What are the formal definitions and notations?",
            "What are the key theoretical results and proofs?",
            "What are the assumptions and conditions?",
            "What are the implications and applications?",
        ],
        "should_answer": [
            "What are the intuitions behind the theory?",
            "What are worked examples demonstrating the theory?",
        ],
        "could_answer": [
            "What are related theoretical frameworks?",
            "What are open theoretical questions?",
        ],
        "retrieval_focus": [
            "Theory papers with proofs and formal analysis",
            "Papers with mathematical foundations",
            "Papers discussing theoretical implications",
        ],
    },
    "benchmark": {
        "must_answer": [
            "What metrics are used to evaluate performance?",
            "What are the quantitative results across methods?",
            "What are the experimental setups and datasets?",
            "What are the key factors affecting performance?",
        ],
        "should_answer": [
            "What are the statistical significance and confidence intervals?",
            "What are the computational costs (time, memory, energy)?",
        ],
        "could_answer": [
            "What are the reproducibility considerations?",
            "What are the limitations of the benchmarks?",
        ],
        "retrieval_focus": [
            "Benchmark papers with quantitative comparisons",
            "Papers with detailed experimental results",
            "Papers discussing evaluation methodologies",
        ],
    },
}

# Default template for queries that don't match specific patterns
DEFAULT_TEMPLATE = {
    "must_answer": [
        "What is the problem being addressed?",
        "What are the main approaches or solutions?",
        "What is the current state of knowledge?",
        "What are the practical implications?",
    ],
    "should_answer": [
        "What are the key empirical findings?",
        "What are representative examples?",
    ],
    "could_answer": [
        "What are related topics or connections?",
        "What are open questions?",
    ],
    "retrieval_focus": [
        "Primary research papers",
        "Survey or review papers",
        "Papers with practical evaluations",
    ],
}


def classify_query(query: str) -> Literal["comparison", "implementation", "survey", "theory", "benchmark", "default"]:
    """Classify query into template type.
    
    Args:
        query: User query string
    
    Returns:
        Template type identifier
    """
    query_lower = query.lower()
    
    # Score each template type
    scores = {}
    for template_type, patterns in PATTERNS.items():
        score = sum(
            1 for pattern in patterns
            if re.search(pattern, query_lower, re.IGNORECASE)
        )
        scores[template_type] = score
    
    # Return highest-scoring template (or default if no matches)
    if max(scores.values()) > 0:
        return max(scores, key=scores.get)
    return "default"


def extract_query_entities(query: str) -> dict[str, list[str]]:
    """Extract key entities from query for template customization.
    
    Args:
        query: User query string
    
    Returns:
        Dict with entity lists (methods, metrics, concepts, etc.)
    """
    # Simple heuristic extraction (can be improved with NER)
    entities = {
        "methods": [],
        "metrics": [],
        "concepts": [],
    }
    
    # Extract quoted terms as key entities
    quoted = re.findall(r'"([^"]+)"', query)
    entities["concepts"].extend(quoted)
    
    # Extract capitalized terms (likely method names)
    caps = re.findall(r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b', query)
    entities["methods"].extend([c for c in caps if len(c) > 3 and c not in {"What", "How", "Why", "Which", "When"}])
    
    # Extract metric keywords
    metric_keywords = ["accuracy", "latency", "throughput", "F1", "precision", "recall", "AUC"]
    for keyword in metric_keywords:
        if re.search(rf'\b{keyword}\b', query, re.IGNORECASE):
            entities["metrics"].append(keyword)
    
    return entities


def customize_template(template: dict, query: str, entities: dict) -> dict:
    """Customize template questions with query-specific terms.
    
    Args:
        template: Base template dict
        query: Original user query
        entities: Extracted entities from query
    
    Returns:
        Customized template
    """
    # For now, just return base template
    # In production, could inject entity names into questions
    # e.g., "What are the key differences between {method1} and {method2}?"
    
    return {
        **template,
        "original_query": query,
        "extracted_entities": entities,
    }


def generate_plan(query: str) -> dict[str, Any]:
    """Generate structured plan from template.
    
    Args:
        query: User query string
    
    Returns:
        Structured plan dict with must/should/could questions
    """
    # Classify query
    template_type = classify_query(query)
    
    # Get template
    if template_type == "default":
        template = DEFAULT_TEMPLATE
    else:
        template = TEMPLATES[template_type]
    
    # Extract entities for customization
    entities = extract_query_entities(query)
    
    # Customize template
    customized = customize_template(template, query, entities)
    
    # Add metadata
    plan = {
        "query": query,
        "template_type": template_type,
        "must_answer": customized["must_answer"],
        "should_answer": customized["should_answer"],
        "could_answer": customized["could_answer"],
        "retrieval_focus": customized["retrieval_focus"],
        "extracted_entities": entities,
        "total_questions": (
            len(customized["must_answer"]) +
            len(customized["should_answer"]) +
            len(customized["could_answer"])
        ),
    }
    
    return plan


def validate_plan_completeness(plan: dict) -> tuple[bool, str]:
    """Validate that plan is complete (deterministic check).
    
    Args:
        plan: Generated plan dict
    
    Returns:
        (is_complete, reason)
    """
    required_fields = ["must_answer", "should_answer", "could_answer", "retrieval_focus"]
    
    for field in required_fields:
        if field not in plan:
            return (False, f"Missing required field: {field}")
        if not plan[field]:
            return (False, f"Empty required field: {field}")
    
    # Check minimum question counts
    if len(plan["must_answer"]) < 3:
        return (False, f"Too few must_answer questions: {len(plan['must_answer'])} < 3")
    
    if len(plan["retrieval_focus"]) < 2:
        return (False, f"Too few retrieval_focus items: {len(plan['retrieval_focus'])} < 2")
    
    return (True, "Plan complete")


def plan_to_dimensions(plan: dict) -> list[str]:
    """Convert plan to dimension list for coverage tracking.
    
    Args:
        plan: Generated plan dict
    
    Returns:
        List of dimension strings for coverage.py
    """
    dimensions = []
    
    # Must-answer questions become dimensions
    for i, question in enumerate(plan["must_answer"], 1):
        # Extract key concept from question (simple heuristic)
        # e.g., "What are the key differences?" → "key differences"
        match = re.search(r'(what|how|why)\s+(are|is|does)\s+(?:the\s+)?(.+?)\?', question, re.IGNORECASE)
        if match:
            concept = match.group(3).strip()
            dimensions.append(f"must_{i}_{concept[:30]}")
        else:
            dimensions.append(f"must_{i}")
    
    return dimensions


# Integration point for planner node
def replace_planner_llm_with_template():
    """
    Integration pseudo-code for planner.py:
    
    # BEFORE (LLM-based with self-grading):
    def planner_node(state):
        query = state['query']
        
        # LLM generates plan
        plan_response = llm.invoke(planner_prompt(query))
        plan = parse_plan(plan_response)
        
        # LLM self-grades plan (UNRELIABLE)
        quality_score = llm.invoke(grade_plan_prompt(plan))
        
        return {"plan": plan, "plan_quality": quality_score}
    
    # AFTER (Template-driven with deterministic validation):
    def planner_node(state):
        from app.maintenance.planner_templates import generate_plan, validate_plan_completeness
        
        query = state['query']
        
        # Generate plan from template (NO LLM)
        plan = generate_plan(query)
        
        # Validate completeness (DETERMINISTIC)
        is_complete, reason = validate_plan_completeness(plan)
        
        if not is_complete:
            event("plan_incomplete", reason=reason)
            # Could fallback to LLM here, or fail fast
        
        return {
            "plan": plan,
            "plan_complete": is_complete,
            "plan_validation": reason
        }
    """
    pass
