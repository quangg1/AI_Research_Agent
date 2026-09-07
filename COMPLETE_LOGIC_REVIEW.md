# LOGIC ERROR ANALYSIS - ALL FIXES

## Fix 1: Domain-Balanced Retrieval ❌ CRITICAL ERROR

### Error: Backfill defeats max_code_ratio
```python
# Scenario: 20 papers, 16 code (80%), 4 non-code (20%)
max_code = 8  # 40%
balanced = 8 code + 4 non-code = 12 total
# Backfill to reach 20:
balanced += 8 more code → 16 code total (80%!)
```

**Impact**: Doesn't fix coding skew when input is heavily biased!

**Fix Required**: Remove backfill OR cap total code at 50% absolute max

---

## Fix 2: Quality-Aware Smart Stopping - REVIEW

### Logic Flow:
```python
def after_critic(state):
    # Early stop 1: Quality excellent
    if must_pct >= 75 and depth_score >= 80:
        return "hitl"  # STOP
    
    # Early stop 2: Stagnation (3 iterations)
    if len(quality_history) >= 3:
        recent = quality_history[-3:]
        sources_stagnant = recent[0]["unique_sources"] == recent[1] == recent[2]
        coverage_stagnant = abs(recent[2]["must_pct"] - recent[1]) < 5 and abs(recent[1] - recent[0]) < 5
        score_stagnant = abs(recent[2]["depth_score"] - recent[1]) < 3 and abs(recent[1] - recent[0]) < 3
        
        if sources_stagnant and coverage_stagnant and score_stagnant:
            return "hitl"  # STOP
    
    # Continue if improving
    return "planner"  # LOOP
```

### Potential Issues:

#### Issue 2.1: quality_history not initialized ⚠️ MINOR
```python
quality_history = state.get("_quality_history") or []
quality_history.append({...})
```
**Problem**: If critic runs before quality_history exists, first append creates length=1.
Second run: length=2. Third run: length=3 → check runs.
**Status**: CORRECT - no error, just takes 3 iterations to detect

#### Issue 2.2: Stagnation thresholds too strict? ⚠️ DESIGN CHOICE
```python
coverage_stagnant = abs(diff) < 5  # 5% change
score_stagnant = abs(diff) < 3     # 3 points change
```
**Scenario**: Coverage goes 60% → 64% → 68% (improving by 4% each time)
- abs(68-64) = 4 < 5 → stagnant? YES
- abs(64-60) = 4 < 5 → stagnant? YES
- Would trigger stagnation even though improving!

**This is a LOGIC ERROR**: Should be checking if BOTH recent diffs are <5, not if each individual diff is <5.

**Fix Required**:
```python
# WRONG (current):
coverage_stagnant = abs(recent[2] - recent[1]) < 5 and abs(recent[1] - recent[0]) < 5

# RIGHT (should be):
coverage_stagnant = (
    abs(recent[2]["must_pct"] - recent[1]["must_pct"]) < 3  # Less strict
    and abs(recent[1]["must_pct"] - recent[0]["must_pct"]) < 3
)
```
**Threshold 5% is too high** - normal improvement is ~4-8% per iteration!

#### Issue 2.3: Early stop at 75%/80 - good or bad? ✅ OK
- If coverage=75%, score=80 → stops
- This seems reasonable (good quality already)
- User wants dynamic stopping (stop when good enough) ✅

---

## Fix 3: Enhanced Followup Queries - REVIEW

### Logic Flow:
```python
def followups_for_gaps(query, coverage):
    entities = entity_candidates(user_goal(query), limit=8)
    
    for gap in ordered[:limit]:
        gap_with_patterns = dict(gap)
        patterns = gap.get("patterns") or []
        
        # Add patterns to followup
        if patterns and not gap.get("followup"):
            pattern_text = " ".join(patterns[:3])
            gap_with_patterns["followup"] = f"{gap_label}: {pattern_text}"
        
        # Route by gap type
        if "implement" in gap_id:
            gap_with_patterns["agent_hint"] = "search_implementation"
        
        question, agent = rewrite_gap_query(query, gap_with_patterns)
        
        # Override agent
        if hint.startswith("scholar"):
            agent = AgentName.SCHOLAR
        
        # Add prefix
        if agent == SCHOLAR and entities:
            question = f"arxiv papers: {entity_str} {question}"
```

### Potential Issues:

#### Issue 3.1: agent_hint overrides rewrite_gap_query ⚠️ MINOR
```python
question, agent = rewrite_gap_query(...)  # Returns agent
# Then immediately override:
if hint.startswith("scholar"):
    agent = AgentName.SCHOLAR  # Overrides!
```
**Status**: This is intentional - we want to force routing based on gap type.
**But**: What if rewrite_gap_query chose SEARCH for good reason? We ignore it.
**Impact**: Minor - routing hints are probably correct most of the time.

#### Issue 3.2: Prefix may create too-specific queries ⚠️ DESIGN RISK
```python
if agent == SCHOLAR:
    question = f"arxiv papers: {entity_str} {question}"
```
**Scenario**: 
- Original question: "memory architecture comparison"
- After rewrite: "How do LangGraph and AutoGen manage state?"
- After prefix: "arxiv papers: LangGraph AutoGen How do LangGraph and AutoGen manage state?"
- Result: Redundant entity names, possibly confusing query

**Status**: Might work, might be redundant. Not a logic ERROR, but could be improved.

---

## Fix 4: Separate Retrieval vs Generation Checks - REVIEW

### Logic Flow:
```python
def check_memo_quality(body, evidence=None, coverage=None):
    # Check generation issues
    should_regenerate = (
        duplicate_ratio > 0.40
        or stacking_count >= 2
        or ...
    )
    
    # NEW: Check if retrieval issue
    is_retrieval_issue = False
    if coverage:
        is_retrieval_issue = _is_retrieval_issue(coverage)
        if is_retrieval_issue:
            should_regenerate = False  # DON'T regenerate

def _is_retrieval_issue(coverage):
    must_pct = coverage.get("depth_score", {}).get("must_answer", {}).get("pct") or 0
    if must_pct < 65:
        return True
    
    critical_gaps = coverage.get("critical_gaps") or []
    if critical_gaps:
        return True
    
    primary_sources = coverage.get("primary_sources") or 0
    if primary_sources == 0:
        return True
    
    return False
```

### Potential Issues:

#### Issue 4.1: Threshold conflicts ⚠️ INCONSISTENCY
```python
# In builder.py (early stop):
if must_pct >= 75 and depth_score >= 80:
    return "hitl"  # Quality EXCELLENT, stop

# In memo_quality.py (retrieval issue):
if must_pct < 65:
    return True  # Retrieval issue

# In coverage.py (critic_should_pass):
if must_pct < 60:
    reasons.append("Coverage too low")
```

**We have 3 different thresholds: 60%, 65%, 75%!**

**Question**: What happens at must_pct=64%?
- Early stop? NO (< 75%)
- Retrieval issue? YES (< 65%) → skip regeneration
- Critic pass? YES (>= 60%)

→ Critic passes, goes to report, generates memo, memo_gate says "retrieval issue", doesn't regenerate, publishes mediocre memo!

**This is an INCONSISTENCY** - thresholds don't align!

**Fix Required**: Use consistent thresholds or have clear ranges:
```python
# Suggested:
# < 60%: FAIL critic, loop
# 60-74%: PASS critic, but flag as retrieval issue (no regeneration)
# >= 75%: EXCELLENT, early stop
```

#### Issue 4.2: What if BOTH retrieval AND generation issues? ⚠️ EDGE CASE
```python
# Scenario: must_pct=50% (retrieval) AND duplicate_ratio=0.5 (generation)
is_retrieval_issue = True  # < 65%
should_regenerate = True  # duplicate > 0.40

# Then:
if is_retrieval_issue:
    should_regenerate = False  # Override!
```

**Result**: We skip fixing duplicate quotes because coverage is low!

**Is this correct?** 
- PRO: Don't waste regeneration when evidence is bad
- CON: We publish memo with duplicate quotes (poor quality)

**Decision**: Probably OK - if coverage is bad, memo will be bad anyway. Fixing duplicates won't help much.

---

## SUMMARY OF LOGIC ERRORS

### ❌ CRITICAL (Must Fix):
1. **Fix 1**: Backfill defeats max_code_ratio constraint
2. **Fix 2**: Stagnation threshold too high (5% catches normal improvement)
4. **Fix 4**: Threshold inconsistency (60% vs 65% vs 75%)

### ⚠️ MINOR (Should Consider):
3. **Fix 2**: quality_history initialization (OK, just takes 3 iterations)
5. **Fix 3**: Agent hint overrides rewrite (intentional, minor risk)
6. **Fix 3**: Prefix redundancy (cosmetic, not breaking)
7. **Fix 4**: Both retrieval+generation issues (edge case, acceptable tradeoff)

---

## RECOMMENDATION

**MUST FIX NOW**:
1. Fix domain balance backfill logic
2. Lower stagnation threshold (5% → 3%)
3. Align coverage thresholds across modules

**CAN DEFER**:
- Prefix redundancy (minor)
- Agent hint override (intentional)
- Dual issues handling (rare edge case)
