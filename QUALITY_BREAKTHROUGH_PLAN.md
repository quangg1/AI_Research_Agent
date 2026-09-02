# Quality Breakthrough Plan

## Current State Analysis

### ✅ What's Working (DEFENSIVE fixes):
1. Citation stacking detector
2. Source saturation detector  
3. Duplicate quote checker
4. Quality loop limit (MAX=2)
5. Stagnation detection
6. Scholar always included
7. Per-dimension retrieval (k=5 per slot)
8. Per-dimension evidence reranking with embeddings

### ❌ What's Missing (OFFENSIVE quality):

## ROOT CAUSE: Generic Dimension Decomposition

**Current dimensions (heuristic templates):**
```python
# decompose.py line 50-200
TEMPLATES = [
    {"id": "quantitative", "label": "Measured numbers, benchmarks, latency, or cost"},
    {"id": "scalability", "label": "Scalability: KV-cache, GPU memory bandwidth"},
    {"id": "constraints", "label": "Limits, bottlenecks, and failure modes"},
    {"id": "worked_example", "label": "Concrete worked example or system walkthrough"},
    # ...
]
```

**Problem:** These are **textbook topics**, NOT **paper-specific findings**!

**What reference PDF has (specific dimensions):**
- "Multi-Stage Agent Pipelines deploying specialized functional roles"
- "Hallucination and Fat-Tailed Knowledge Truncation in LLMs"
- "Active Label Curation and FreeAL Mechanisms"  
- "Domain-Adapted RAGAS Metrics for Validation"
- "Autophagous Degradation and Model Collapse Prevention"

→ **These are PAPER-SPECIFIC concepts, not generic taxonomy!**

## Breakthrough Strategy

### Phase 1: Evidence-First Dimension Decomposition ⭐⭐⭐

**Current flow:**
```
Query → Generic Templates → Retrieve Evidence → Write Sections
         ❌ WRONG ORDER!
```

**Better flow:**
```
Query → Scholar + Search → Scan Papers → Extract Key Concepts → Create Dimensions → Targeted Retrieval → Write
        ✅ EVIDENCE-FIRST!
```

**Implementation:**
1. **After initial scholar/search**, run "paper concept extraction":
   - Scan abstracts + h2/h3 headings from top 5 papers
   - Extract key technical concepts (not generic topics)
   - Example: "FreeAL active verifier", "RAGAS faithfulness metric", "multi-agent curation pipeline"
   
2. **LLM dimension synthesis** from extracted concepts:
   ```
   Prompt: "From these paper concepts:
   - Multi-stage agentic frameworks
   - FreeAL active verifier mechanisms  
   - RAGAS domain-adapted metrics
   - Autophagous degradation detection
   
   Create 4-6 must-answer dimensions that:
   - Are paper-specific (not textbook topics)
   - Cover distinct aspects
   - Can be evaluated with concrete findings
   "
   ```

3. **Dimension validation**:
   - Reject dimensions that are too generic ("Performance", "Challenges")
   - Require each dimension to reference a specific technique/paper concept
   - Min 3 pattern keywords per dimension derived from paper terminology

### Phase 2: Numeric Grounding Improvements ⭐⭐

**Current problem:** Numbers mentioned but not properly contextualized

**Fix:**
1. **Stricter numeric extraction template:**
   ```
   BAD:  "15% accuracy improvement [1]"
   GOOD: "15% accuracy improvement on specialized domains 
          when using GPT-4 generated examples with human validation [1]"
   ```

2. **Enforce metric + condition + domain** in adversarial.py:
   ```python
   def validate_quantitative_claim(text: str) -> bool:
       has_metric = bool(NUMBER_WITH_UNIT_RE.search(text))
       has_condition = bool(CONDITION_RE.search(text))  # "on X domain", "with Y setup"
       has_method = bool(METHOD_RE.search(text))  # "using Z", "via W"
       return has_metric and has_condition and has_method
   ```

### Phase 3: Writer Instruction Enhancements ⭐

**Add to writer prompt:**
```
"For each ### dimension subsection:
1. Lead with the paper-specific technique/concept name
2. Define it briefly (1 sentence)
3. Present measured findings WITH conditions
4. Explain why it matters (operational impact)
5. Note limitations from the papers

Example structure:
### Multi-Stage Agent Pipelines with Role-Based Generation
Technical definition: Specialized generator models execute distinct 
functional roles (question diversifier, procedural clarifier, contextual 
grounding agent) within closed-loop synthesis workflows [2].

Measured impact: Achieves 92% faithfulness score vs 78% for single-prompt 
baseline on telecommunications domain QA pairs [2].

Operational design: Four-phase workflow...
```

## Implementation Priority

### MUST DO (Breakthrough Impact):
1. ✅ Evidence-first dimension decomposition
2. ✅ Paper concept extraction from scholar results
3. ✅ LLM dimension synthesis from paper concepts

### SHOULD DO (Quality Polish):
4. ⭐ Stricter numeric validation (metric + condition + domain)
5. ⭐ Writer subsection structure guidance
6. ⭐ Better decision rule extraction (separate empirical vs heuristic)

### NICE TO HAVE:
7. Multi-pass dimension refinement based on evidence quality
8. Automatic dimension merging when too similar
9. Cross-paper concept linking

## Success Metrics

**Before (current output):**
- Generic dimensions: "Data Generation Methodologies", "Model Collapse Detection"
- Vague claims: "high-fidelity datasets", "small verifier models"
- Decision rules: "threshold must be re-benchmarked" (no actual threshold!)

**After (breakthrough):**
- Specific dimensions: "FreeAL Active Curation Mechanisms", "RAGAS Domain-Adapted Metrics"
- Grounded claims: "92% faithfulness score vs 78% baseline on telecom QA pairs [2]"
- Decision rules: "Maintain RAGAS faithfulness ≥ 0.85 on domain validation set before SFT ingestion [2]"

## Next Steps

1. User shares reference PDF → Extract its dimensions → Reverse-engineer pattern
2. Implement paper concept extraction in new module: `apps/agent/app/domain/paper_concepts.py`
3. Replace `derive_slots` heuristics with evidence-first LLM decomposition
4. Test on same query → Compare old vs new dimensions
5. Measure breakthrough: specific vs generic dimension ratio

---

**Key Insight:** Defensive fixes prevent bad memos. Evidence-first dimensions CREATE GOOD memos.
