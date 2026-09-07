# Generation Quality Fixes - Architecture Analysis

## 🎯 Problem Statement

**11 fixes (324 lines) improved defenses but NOT generation quality.**

### Evidence (Output #3 Verdict):
- Score: 5.5/10 (no improvement from baseline 5.5)
- Coding skew: Still heavy (SWE-Bench/Docker dominate)
- Worked example: Still composites (RepoLaunch+SSR+OpenSSL)
- Conceptual errors: SSR mechanism described wrong
- Missing factuality metrics: No hallucination measurement
- Memory depth: Weak (survey coverage, no per-system depth)

---

## 🔍 Root Cause: All Fixes Are POST-Generation

| Fix Type | What It Does | What It Doesn't Do |
|----------|-------------|-------------------|
| Entity recovery (#1) | Finds entities in query | Doesn't retrieve depth per entity |
| Coverage gate (#2) | Blocks if missing | Doesn't ensure quality of found evidence |
| Number verify (#2 post) | Catches misattributed numbers | Doesn't prevent writer from making conceptual errors |
| Citation relevance (#6) | Strips off-topic papers | Doesn't balance domains in retrieval |
| Composite label (#3) | Labels multi-source examples | Doesn't prevent compositing |

**Conclusion:** We're catching bad outputs, not generating good ones.

---

## 🏗️ Generation-Level Fixes Required

### Fix G1: Domain-Balanced Retrieval ⭐ HIGH PRIORITY
**Problem:** Scholar search returns coding-heavy results for any query

**Current Flow:**
```
query: "memory tools reflection in agents"
  ↓
scholar_search("memory tools reflection in agents")
  ↓
Returns: 90% SWE-Bench/Docker/coding papers
  ↓
Writer sees mostly code → writes coding-skewed memo
```

**Fix:**
```python
# In collector.py or scholar.py
def domain_balanced_search(query, dimensions=['memory', 'tools', 'reflection']):
    """
    For multi-dimension queries, retrieve k results PER dimension.
    Prevents one domain (coding) from dominating.
    """
    all_results = []
    
    for dim in dimensions:
        # Targeted search per dimension
        dim_query = f"{query} {dim} -code -docker -build"
        dim_results = hybrid_retrieve(dim_query, k=3)
        
        # Tag with dimension for downstream filtering
        for r in dim_results:
            r['target_dimension'] = dim
        
        all_results.extend(dim_results)
    
    # Dedupe but preserve dimension balance
    return balance_by_dimension(all_results, max_per_dim=3)
```

**Impact:**
- Coding skew: Heavy → Balanced
- Coverage: Aggregate → Per-dimension
- Expected Δ: +1.5 points (Faithfulness + Completeness)

**Effort:** ~50 lines in `scholar.py` + `collector.py`

---

### Fix G2: Single-Source Worked Example Enforcement ⭐ HIGH PRIORITY
**Problem:** Writer composites multiple sources, fix #3 only labels it

**Current Flow:**
```
Writer sees: [RepoLaunch doc] + [SSR paper] + [SWE-Bench++ paper]
  ↓
Writer: "The pipeline does X (RepoLaunch) then Y (SSR) then Z (SWE++)"
  ↓
Fix #3: Adds "Composite" label
  ↓
Result: Composite example published with label
```

**Fix:**
```python
# 1. Prompt enforcement (deep_write.py)
"""
## Worked example
MANDATORY: Draw ALL steps from ONE source [n] only.

If you cannot find a complete walkthrough in one source:
- Write: "No complete single-source walkthrough found."
- Explain what's missing.
- DO NOT composite steps from multiple papers.

Citations with ≥2 [n] will be rejected.
"""

# 2. Hard block (report_integrity.py)
def enforce_single_source_worked_example(body):
    worked = _section(body, "Worked example")
    if not worked:
        return body
    
    cite_nums = set(extract_citation_numbers(worked))
    
    if len(cite_nums) >= 2:
        # DROP section entirely, don't just label
        body = _drop_section(body, "Worked example")
        # Add limitation note
        return body, ["Worked example removed: cited multiple sources"]
    
    return body, []
```

**Impact:**
- Synthesis honesty: 6.5 → 8.5 (+2.0)
- Prevents fabricated pipelines
- Expected Δ: +0.8 points

**Effort:** ~30 lines (prompt + enforcement)

---

### Fix G3: Conceptual Accuracy Verification
**Problem:** Writer describes mechanisms wrong (SSR truncation vs reflection)

**Current Flow:**
```
Writer LLM: "SSR uses truncation to intervene at weak steps"
  ↓
(No check exists for conceptual accuracy)
  ↓
Published: Wrong mechanism description
```

**Fix:**
```python
# New module: concept_verify.py

def verify_mechanism_claim(claim_text, paper_id, mechanism_name, evidence):
    """
    Cross-reference mechanism description against paper content.
    Uses LLM to detect conceptual mismatches.
    """
    paper_content = find_evidence_by_id(paper_id, evidence)
    
    prompt = f"""
    Paper [{paper_id}] content: {paper_content}
    
    Claim in memo: "{claim_text}"
    
    Is this claim an accurate description of the mechanism in the paper?
    - If YES: respond "ACCURATE"
    - If NO: respond "INACCURATE: [brief correction]"
    """
    
    response = llm_judge(prompt, model="gpt-4")
    
    if "INACCURATE" in response:
        return False, response.replace("INACCURATE:", "").strip()
    
    return True, ""

# In enforce_report_integrity:
for section in ["Detailed analysis", "Worked example"]:
    claims = extract_mechanism_claims(section)
    for claim in claims:
        is_accurate, correction = verify_mechanism_claim(...)
        if not is_accurate:
            issues.append(f"Mechanism misdescribed: {correction}")
```

**Impact:**
- Catches SSR truncation vs reflection errors
- Factuality: 7.5 → 8.5 (+1.0)
- Expected Δ: +0.4 points

**Effort:** ~100 lines + LLM call overhead

**Risk:** High (LLM judge reliability, latency)

---

### Fix G4: Factuality Metrics Pipeline
**Problem:** No measurement of hallucination / groundedness

**Current:**
```
Confidence breakdown: {
  must_answer: 70%,
  primary_sources: 80%,
  // No factuality metric
}
```

**Fix:**
```python
# New module: factuality_metrics.py

def compute_factuality_score(memo_body, evidence, citations):
    """
    Extract claims, verify against evidence, return grounding ratio.
    """
    # Extract factual claims (not opinions/interpretations)
    claims = extract_factual_claims(memo_body)
    
    grounded_count = 0
    hallucinated_count = 0
    
    for claim in claims:
        cite_nums = extract_citations_for_claim(claim, memo_body)
        cited_evidence = [e for e in evidence if e['n'] in cite_nums]
        
        if is_claim_grounded_in_evidence(claim, cited_evidence):
            grounded_count += 1
        else:
            hallucinated_count += 1
    
    return {
        'total_claims': len(claims),
        'grounded_claims': grounded_count,
        'hallucinated_claims': hallucinated_count,
        'factuality_score': grounded_count / len(claims) if claims else 0.0,
    }

# Add to confidence breakdown
confidence_breakdown['factuality'] = {'pct': int(factuality_score * 100)}
```

**Impact:**
- Addresses missing factuality metric in verdict
- Enables gating on factuality < 70%
- Expected Δ: +0.3 points (transparency, not quality)

**Effort:** ~150 lines

---

### Fix G5: Per-Concept Research Depth
**Problem:** Aggregate coverage hides per-entity weakness

**Current:**
```
Slots: [
  "Memory architectures",      # weak - only TUM survey
  "Tool-use strategies",        # stronger - Docker/RepoLaunch
  "Iterative reflection",       # partial - SSR
]

Coverage: 2/3 covered → 67% → "standard"
```

**Reality:** Memory weak, tools strong → aggregate hides weakness

**Fix:**
```python
# In decompose.py: Per-entity × per-dimension matrix

def derive_entity_dimension_slots(query):
    """
    For comparison queries with named entities:
    Create (entity × dimension) matrix slots.
    """
    entities = named_systems(query)  # ["OpenAI Agents", "LangGraph", "AutoGen"]
    dimensions = extract_dimensions(query)  # ["memory", "tools", "reflection"]
    
    slots = []
    for entity in entities:
        for dim in dimensions:
            slots.append({
                'id': f"{entity}_{dim}",
                'label': f"{entity}: {dim}",
                'entity': entity,
                'dimension': dim,
                'patterns': [entity, dim],
            })
    
    return slots

# In coverage.py: Matrix scoring

def score_entity_dimension_matrix(slots):
    """
    Coverage = filled cells / total cells
    Reject if any entity has <50% of its dimensions covered.
    """
    matrix = {}
    for slot in slots:
        entity = slot['entity']
        dim = slot['dimension']
        matrix.setdefault(entity, {})[dim] = slot['status']
    
    # Per-entity coverage
    for entity, dims in matrix.items():
        covered = sum(1 for s in dims.values() if s == 'covered')
        entity_coverage = covered / len(dims)
        
        if entity_coverage < 0.5:
            issues.append(f"Entity {entity} coverage {int(entity_coverage*100)}% (<50%)")
    
    # Overall matrix coverage
    all_cells = sum(len(dims) for dims in matrix.values())
    covered_cells = sum(1 for dims in matrix.values() for s in dims.values() if s == 'covered')
    
    return covered_cells / all_cells
```

**Impact:**
- Memory depth: Weak → Per-entity required
- Faithfulness: 5.0 → 7.0 (+2.0)
- Expected Δ: +1.0 points

**Effort:** ~80 lines

---

## 📊 Priority & ROI

| Fix | Impact (Δ) | Effort | Risk | Priority |
|-----|-----------|--------|------|----------|
| **G1** Domain balance | +1.5 | Medium | Low | **P0** |
| **G2** Single-source enforce | +0.8 | Low | Low | **P0** |
| **G5** Per-concept depth | +1.0 | High | Medium | **P1** |
| **G4** Factuality metrics | +0.3 | High | Low | **P2** |
| **G3** Concept verify | +0.4 | Very High | High | **P3** |

**Total Expected:** 5.5 → 9.0+ (if all implemented)

**Realistic (P0 + P1):** 5.5 → 8.3

---

## 🎯 Recommended Implementation Order

### Phase 1: Quick Wins (P0) - ~80 lines, 1-2 days
1. **G2** Single-source enforcement (30 lines)
   - Prompt update in `deep_write.py`
   - Hard block in `report_integrity.py`
   
2. **G1** Domain-balanced retrieval (50 lines)
   - Per-dimension search in `scholar.py`
   - Balance in `collector.py`

**Expected: 5.5 → 7.3 (+1.8)**

### Phase 2: Depth (P1) - ~80 lines, 2-3 days
3. **G5** Per-concept depth matrix
   - Entity × dimension slots in `decompose.py`
   - Matrix scoring in `coverage.py`

**Expected: 7.3 → 8.3 (+1.0)**

### Phase 3: Metrics (P2) - ~150 lines, 3-4 days
4. **G4** Factuality pipeline
   - Claim extraction + verification
   - Add to confidence breakdown

**Expected: 8.3 → 8.6 (+0.3 transparency)**

### Phase 4: Advanced (P3) - ~100 lines, 4-5 days
5. **G3** Conceptual accuracy
   - LLM-based mechanism verification
   - High latency, requires tuning

**Expected: 8.6 → 9.0 (+0.4)**

---

## 🚨 Key Insight

**Previous 11 fixes were necessary but not sufficient:**
- They stop bad memos from getting worse
- They don't make memos better

**These 5 fixes are generative:**
- They improve what goes INTO the writer
- They constrain what comes OUT of the writer
- They measure what SHOULD be in the output

---

## 💡 Architectural Lesson

### Wrong Approach (What We Did):
```
Bad Input → Writer → Bad Output → [11 Guards Block Some] → Mediocre Published
```

### Right Approach (What We Need):
```
Query → [Domain Balance] → Good Input → Writer → [Single-Source Enforce] → Good Output
                                          ↓
                                    [Concept Verify]
                                          ↓
                                    [Factuality Measure]
```

---

## 🎯 Next Steps

1. **Decide:** Phase 1 only (quick +1.8) or Phase 1+2 (slower +2.8)?
2. **Implement:** I'll code G2 + G1 first (highest ROI)
3. **Test:** Run on same query, compare verdict
4. **Iterate:** Adjust based on real output

**Do you want me to implement Phase 1 (G2 + G1) now?**
