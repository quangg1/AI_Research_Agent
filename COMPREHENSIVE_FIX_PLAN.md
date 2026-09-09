# COMPREHENSIVE FIX PLAN
## Kiln Memo Quality Improvement - Holistic System Design

**Mục tiêu**: Cải thiện quality từ 5.5/10 → 8+/10 bằng fixes có tính hệ thống, không vá víu

---

## I. DEPENDENCY MAP (176 Python files)

### Core Modules & Their Dependents:

```
coverage.py (26 imports)
├── critic.py ✓
├── report.py ✓
├── collector.py ✓
├── extract.py ✓
├── planner.py ✓
├── briefing.py
└── evidence_filter.py

memo_quality.py (2 imports)
├── memo_gate.py ✓✓
└── tests/test_memo_quality_fixes.py

scholar.py (no imports - source node)
└── → feeds collector → retrieve → critic

search.py (no imports - source node)
└── → feeds collector → retrieve → critic

builder.py (graph routing)
├── after_critic() → decides loop or stop
├── after_memo_gate() → decides quality regeneration
└── defines entire graph flow
```

### Impact Chains:

```
Fix 1: Domain Balance
scholar.py → collector.py → retrieve.py → critic.py → [all downstream]
search.py  ↗

Fix 2: Quality Loop Separation  
memo_quality.py → memo_gate.py → builder.after_memo_gate()
                                → report.py (regeneration target)

Fix 3: Followup Specificity
coverage.followups_for_gaps() → critic.py → planner.py → scholar/search
```

---

## II. ROOT CAUSES (Verified từ code)

### 1. Coding Skew (70% code papers)
**Nguồn**: `scholar_node._openalex()` + `search_node._search()`
- OpenAlex/S2 naturally return implementation papers
- Tavily search returns GitHub/code-heavy results
- **KHÔNG CÓ** domain balancing logic

**Tác động downstream**:
```
70% code papers in pool
  ↓
retrieve_node: per-dim retrieval from biased pool
  ↓ k=5 per dim, but 3-4/5 are still code
critic: coverage thấp (missing conceptual/theory papers)
  ↓ triggers loop
6 iterations × biased retrieval = still 70% code
  ↓
report: weak conceptual analysis (5.5/10)
```

### 2. Wasteful Quality Loop
**Nguồn**: `memo_quality.check_memo_quality()` không phân biệt retrieval vs generation issues

**Current logic**:
```python
# memo_quality.py:212-219
should_regenerate = (
    duplicate_ratio > 0.40      # ✅ generation - CÓ THỂ SỬA
    or stacking_count >= 2      # ✅ generation - CÓ THỂ SỬA
    or saturation_count >= 2    # ✅ generation - CÓ THỂ SỬA
    or filler_count >= 1        # ✅ generation - CÓ THỂ SỬA
    or placeholder_count > 0    # ✅ generation - CÓ THỂ SỬA
)

# BUT memo_gate ALSO regenerates when memo shows poor coverage
# → Vô ích vì coverage là retrieval issue!
```

**Tác động**:
- 6 iterations × 3 memo rewrites = 18 memo generations
- Chỉ 5/18 có ý nghĩa (generation issues)
- 13/18 là waste (coverage issues không sửa được bằng regeneration)

### 3. Generic Followup Queries
**Nguồn**: `coverage.followups_for_gaps()` line 542-565

**Current**:
```python
for gap in ordered[:limit]:
    question = f"Evidence for {gap.label}"  # Quá generic!
    # Example: "Evidence for memory architecture"
```

**Tác động**:
- Generic query → scholar/search return same biased results
- Không cải thiện coverage quality qua iterations
- Entity-specific papers không được retrieve

---

## III. COMPREHENSIVE FIX DESIGN

### Fix 1: Domain-Balanced Retrieval ⭐⭐⭐ (HIGHEST IMPACT)

**Objective**: Đảm bảo evidence pool có diversity: code, theory, benchmarks, docs

**Files to modify**:
1. `apps/agent/app/graph/nodes/scholar.py`
2. `apps/agent/app/graph/nodes/search.py`

**Design**:
```python
def _balanced_evidence_pool(papers: list[dict], max_code_ratio: float = 0.40) -> list[dict]:
    """Enforce domain balance at SOURCE (scholar/search results).
    
    Strategy:
    1. Classify papers by domain: code, theory, benchmarks, docs
    2. Limit each domain to max % of total
    3. Prefer primary sources (arxiv, ACM, IEEE) over secondary
    
    Args:
        papers: Raw results from OpenAlex/Tavily
        max_code_ratio: Max % of code/implementation papers (default 40%)
    
    Returns:
        Balanced evidence pool with enforced diversity
    """
    code_papers = []
    theory_papers = []
    benchmark_papers = []
    doc_papers = []
    
    for p in papers:
        domain = _classify_paper_domain(p)
        if domain == "code":
            code_papers.append(p)
        elif domain == "theory":
            theory_papers.append(p)
        elif domain == "benchmark":
            benchmark_papers.append(p)
        else:
            doc_papers.append(p)
    
    total = len(papers)
    max_code = int(total * max_code_ratio)
    
    # Build balanced pool
    balanced = []
    balanced.extend(code_papers[:max_code])
    balanced.extend(theory_papers)
    balanced.extend(benchmark_papers)
    balanced.extend(doc_papers)
    
    # If we don't have enough, backfill with remaining code papers
    if len(balanced) < total:
        remaining_code = code_papers[max_code:]
        balanced.extend(remaining_code[:total - len(balanced)])
    
    return balanced[:total]


def _classify_paper_domain(paper: dict) -> str:
    """Classify paper into domain based on URL, title, venue.
    
    Returns: "code" | "theory" | "benchmark" | "docs"
    """
    url = paper.get("url", "").lower()
    title = paper.get("title", "").lower()
    snippet = paper.get("snippet", "").lower()
    blob = f"{url} {title} {snippet}"
    
    # Code: GitHub, GitLab, implementation
    if any(host in url for host in ["github.com", "gitlab.com", "bitbucket.org"]):
        return "code"
    if re.search(r"\b(implementation|source code|library|package)\b", blob):
        return "code"
    
    # Benchmark: evaluation, metrics, comparison
    if re.search(r"\b(benchmark|evaluation|comparison|metric|leaderboard)\b", title):
        return "benchmark"
    
    # Docs: official documentation, API reference
    if any(host in url for host in ["docs.", "documentation", "api."]):
        return "docs"
    if re.search(r"\b(documentation|api reference|guide|tutorial)\b", title):
        return "docs"
    
    # Theory: papers, research, analysis
    if any(host in url for host in ["arxiv.org", "aclanthology.org", "openreview.net"]):
        return "theory"
    
    return "theory"  # Default to theory for unknown
```

**Integration points**:
```python
# scholar.py:93-116 (_scholar_search)
def _scholar_search(query: str) -> tuple[list[dict], int]:
    openalex = _openalex(q)
    semantic = _semantic_scholar(q) if len(openalex) < 5 else []
    
    raw_results = _dedupe_papers(openalex + semantic)
    
    # NEW: Apply domain balancing
    balanced_results = _balanced_evidence_pool(raw_results, max_code_ratio=0.40)
    
    return balanced_results, calls

# search.py:94-116 (_search)
def _search(query: str) -> tuple[list[dict], int]:
    rows = tavily.search(query)
    
    # NEW: Apply domain balancing
    balanced_rows = _balanced_evidence_pool(rows, max_code_ratio=0.40)
    
    return balanced_rows, calls
```

**Side effects check**:
- ✅ collector_node: Receives balanced pool → OK
- ✅ retrieve_node: Per-dim retrieval on balanced pool → BETTER results
- ✅ critic: Better coverage from diverse evidence → PASSES faster
- ✅ Tests: May need to update expected counts in `test_scholar.py`

**Testing**:
```python
def test_domain_balanced_retrieval():
    """Verify scholar/search return balanced evidence (max 40% code)."""
    papers = [
        {"url": "github.com/...", "title": "Implementation"},  # code
        {"url": "github.com/...", "title": "Source"},  # code
        {"url": "arxiv.org/...", "title": "Theory"},  # theory
        {"url": "arxiv.org/...", "title": "Analysis"},  # theory
        {"url": "aclanthology.org/...", "title": "Benchmark"},  # benchmark
    ]
    
    balanced = _balanced_evidence_pool(papers, max_code_ratio=0.40)
    
    code_count = sum(1 for p in balanced if "github" in p["url"])
    code_ratio = code_count / len(balanced)
    
    assert code_ratio <= 0.40, f"Code ratio {code_ratio} exceeds 40%"
```

---

### Fix 2: Separate Retrieval vs Generation Quality Checks ⭐⭐ (HIGH IMPACT)

**Objective**: Đừng regenerate memo khi vấn đề là thiếu evidence (retrieval issue)

**Files to modify**:
1. `apps/agent/app/domain/memo_quality.py` - Keep only generation checks
2. `apps/agent/app/graph/nodes/memo_gate.py` - Add coverage check filter

**Design**:

```python
# memo_quality.py - ADD NEW FUNCTION
def is_retrieval_issue(quality_check: dict, coverage: dict) -> bool:
    """Determine if quality issues are due to retrieval (unfixable by regeneration).
    
    Retrieval issues:
    - Low coverage (<60%)
    - Missing critical dimensions
    - No primary sources
    - Missing named entities
    
    These should trigger RESEARCH LOOP (more retrieval), not QUALITY LOOP (regeneration).
    
    Returns:
        True if issues are retrieval-related (don't regenerate)
        False if issues are generation-related (can regenerate)
    """
    # Check if coverage is the main issue
    must_pct = (coverage.get("depth_score") or {}).get("must_answer", {}).get("pct") or 0
    if must_pct < 60:
        return True  # Retrieval issue - need more evidence
    
    # Check for missing critical dimensions
    critical_gaps = coverage.get("critical_gaps") or []
    if critical_gaps:
        return True  # Retrieval issue - need dimension-specific evidence
    
    # Check for missing primary sources
    if not coverage.get("primary_sources"):
        return True  # Retrieval issue - need primary papers/docs
    
    # Otherwise, it's a generation issue
    return False


# memo_quality.py - UPDATE check_memo_quality signature
def check_memo_quality(
    body_markdown: str, 
    *, 
    evidence: list[dict] | None = None,
    coverage: dict | None = None  # NEW: add coverage parameter
) -> dict[str, Any]:
    """Check memo quality and return issues that should trigger regeneration.
    
    NOW: Only triggers regeneration for GENERATION issues.
    Retrieval issues (coverage, missing dimensions) are filtered out.
    """
    issues: list[str] = []
    
    # Check 1-5: Generation issues (duplicate quotes, stacking, etc.)
    # ... existing checks ...
    
    should_regenerate = (
        duplicate_ratio > 0.40
        or stacking_count >= 2
        or saturation_count >= 2
        or filler_count >= 1
        or placeholder_count > 0
    )
    
    # NEW: Filter out retrieval issues
    if should_regenerate and coverage:
        quality_result = {
            "should_regenerate": should_regenerate,
            "issues": issues,
            # ... other fields ...
        }
        if is_retrieval_issue(quality_result, coverage):
            # Don't regenerate for retrieval issues
            should_regenerate = False
            issues.append("⚠️ Coverage issues detected - triggering research loop, not memo regeneration")
    
    return {
        "should_regenerate": should_regenerate,
        "issues": issues,
        # ... rest ...
    }
```

**Integration**:
```python
# memo_gate.py:37 - UPDATE to pass coverage
def memo_gate_node(state: ResearchState) -> dict:
    body_markdown = report.get("body_markdown") or ""
    evidence = state.get("retrieved") or state.get("evidence") or []
    
    # NEW: Get coverage for retrieval vs generation check
    critic = state.get("critic") or {}
    coverage = critic.get("coverage") or {}
    
    body_markdown, declutter_n = declutter_citations(body_markdown)
    
    # NEW: Pass coverage to quality check
    quality_check = check_memo_quality(body_markdown, evidence=evidence, coverage=coverage)
    
    # ... rest of logic unchanged ...
```

**Side effects check**:
- ✅ memo_gate_node: Still triggers regeneration for real generation issues
- ✅ critic_node: Coverage issues still trigger research loop (unchanged)
- ✅ builder.after_critic(): Loop logic unchanged
- ⚠️ Tests: Need to update tests that expect regeneration for coverage issues

**Testing**:
```python
def test_quality_loop_skips_retrieval_issues():
    """Quality loop should NOT regenerate for coverage < 60%."""
    memo = "Short memo with no issues but low coverage."
    coverage = {"depth_score": {"must_answer": {"pct": 45}}}
    
    quality = check_memo_quality(memo, coverage=coverage)
    
    assert not quality["should_regenerate"], "Should not regenerate for coverage issue"
    assert any("research loop" in i.lower() for i in quality["issues"])


def test_quality_loop_runs_for_generation_issues():
    """Quality loop SHOULD regenerate for duplicate quotes."""
    memo = """
    ## Analysis
    "This is a quote" [1]
    
    ## More Analysis  
    "This is a quote" [1]
    """
    coverage = {"depth_score": {"must_answer": {"pct": 80}}}  # Good coverage
    
    quality = check_memo_quality(memo, coverage=coverage)
    
    assert quality["should_regenerate"], "Should regenerate for duplicate quotes"
```

---

### Fix 3: Smarter Followup Queries ⭐ (MEDIUM IMPACT)

**Objective**: Followup queries phải specific hơn để retrieve targeted evidence

**Files to modify**:
1. `apps/agent/app/domain/coverage.py` - `followups_for_gaps()` function

**Design**:
```python
# coverage.py:542-565 - REPLACE followups_for_gaps
def followups_for_gaps(
    query: str,
    coverage: dict[str, Any],
    limit: int = 2,
    *,
    use_llm: bool = False,
) -> list[SubQuery]:
    """Generate targeted followup queries for coverage gaps.
    
    NEW: Use dimension patterns and entity names for specific queries.
    
    Strategy:
    1. For entity gaps: "arxiv: {entity} {dimension_patterns}"
    2. For implementation gaps: "github: {entity} source code"
    3. For benchmark gaps: "{entity} benchmark evaluation metrics"
    """
    ordered: list[dict] = list(coverage.get("critical_gaps") or [])
    known = {g.get("id") for g in ordered}
    
    for slot in coverage.get("slots") or []:
        if slot.get("status") in {"open", "weak"} and slot.get("id") not in known:
            ordered.append(slot)
            known.add(slot.get("id"))
    
    out: list[SubQuery] = []
    
    # Extract named entities from query for targeted search
    from app.domain.textutil import entity_candidates
    entities = entity_candidates(user_goal(query), limit=8)
    
    for gap in ordered[:limit]:
        gap_id = gap.get("id") or ""
        gap_label = gap.get("label") or ""
        patterns = [p for p in (gap.get("patterns") or []) if p]
        
        # Build targeted query based on gap type
        if "implement" in gap_id.lower() or "code" in gap_id.lower():
            # Implementation gap: target GitHub/source code
            entity_str = " ".join(entities[:2]) if entities else gap_label
            question = f"github source code: {entity_str}"
            agent = AgentName.SEARCH
            
        elif "benchmark" in gap_id.lower() or "evaluat" in gap_id.lower():
            # Benchmark gap: target evaluation papers
            entity_str = " ".join(entities[:2]) if entities else ""
            patterns_str = " ".join(patterns[:2]) if patterns else ""
            question = f"benchmark evaluation metrics: {entity_str} {patterns_str}".strip()
            agent = AgentName.SCHOLAR
            
        elif entities:
            # Entity-specific gap: target arxiv papers about specific entities
            entity_str = " ".join(entities[:3])
            patterns_str = " ".join(patterns[:3]) if patterns else ""
            question = f"arxiv: {entity_str} {patterns_str} {gap_label}".strip()[:200]
            agent = AgentName.SCHOLAR
            
        else:
            # Generic gap: use patterns for specificity
            patterns_str = " ".join(patterns[:3]) if patterns else gap_label
            question = f"{patterns_str}".strip()[:200]
            agent = AgentName.SCHOLAR
        
        out.append(
            SubQuery(
                agent=agent,
                question=question,
                rationale=f"Fill must-answer gap: {gap_label}",
            )
        )
    
    return out
```

**Side effects check**:
- ✅ critic_node: Gets better followup queries
- ✅ planner_node: Routes better queries to scholar/search
- ✅ scholar_node: More specific queries → better results
- ✅ search_node: Targeted GitHub queries find implementation
- ✅ Tests: May need to update `test_coverage.py` expectations

**Testing**:
```python
def test_followup_queries_are_specific():
    """Followup queries should include patterns and entity names."""
    query = "How do LangGraph and AutoGen differ in memory architecture?"
    coverage = {
        "critical_gaps": [{
            "id": "memory_arch",
            "label": "Memory architecture comparison",
            "patterns": ["memory management", "state persistence"],
        }],
        "slots": [],
    }
    
    followups = followups_for_gaps(query, coverage, limit=1)
    
    assert len(followups) == 1
    question = followups[0].question
    
    # Should include entity names
    assert "LangGraph" in question or "AutoGen" in question
    # Should include patterns
    assert "memory" in question.lower()
    # Should be specific (not just "Evidence for X")
    assert "arxiv:" in question or "github" in question
```

---

## IV. IMPLEMENTATION ORDER

### Phase 1: Foundation (HIGH IMPACT, LOW RISK)
1. ✅ **Fix 1: Domain-Balanced Retrieval**
   - Add `_classify_paper_domain()` to `scholar.py`
   - Add `_balanced_evidence_pool()` to both `scholar.py` and `search.py`
   - Integrate into `_scholar_search()` and `_search()`
   - Write tests: `test_domain_balanced_retrieval()`
   
2. ✅ **Fix 3: Smarter Followup Queries**
   - Replace `followups_for_gaps()` in `coverage.py`
   - Add entity extraction for targeted queries
   - Write tests: `test_followup_queries_are_specific()`

### Phase 2: Quality Loop Optimization (MEDIUM IMPACT, MEDIUM RISK)
3. ✅ **Fix 2: Separate Retrieval vs Generation Checks**
   - Add `is_retrieval_issue()` to `memo_quality.py`
   - Update `check_memo_quality()` signature with coverage param
   - Update `memo_gate_node()` and `memo_gate_node_auto()` to pass coverage
   - Write tests: `test_quality_loop_skips_retrieval_issues()`
   
### Phase 3: Verification
4. ✅ **Run full test suite**
   - Check for regressions
   - Update failing tests if behavior change is intended
   
5. ✅ **E2E integration test**
   - Run with real query about "agentic memory systems"
   - Verify coding skew reduced (<50%)
   - Verify quality improved (>7/10)
   - Verify no infinite loops (completes in <4 iterations)

---

## V. SIDE EFFECT ANALYSIS

### Potential Breaking Changes:
1. **Domain balancing** may reduce total code papers
   - Impact: Systems/architecture queries may get fewer implementation examples
   - Mitigation: Allow higher code ratio (50%) for implementation-heavy queries
   
2. **Quality loop filter** may let some mediocre memos through
   - Impact: Memos with generation issues AND coverage issues won't regenerate
   - Mitigation: Check both conditions - only skip if PURELY coverage issue
   
3. **Followup specificity** may be too narrow
   - Impact: Overly specific queries may miss broader context
   - Mitigation: Include both specific (entity + patterns) and generic (label) terms

### Test Files Needing Updates:
- `test_scholar.py` - May need updated expected result counts
- `test_memo_quality_fixes.py` - Need to pass coverage param
- `test_coverage.py` - May need updated followup query expectations
- `test_graph_coverage_gate.py` - May need updated gate behavior

---

## VI. SUCCESS METRICS

### Before (Verified from Verdict #3):
- Coding skew: 70% (15/20 code papers)
- Coverage: 52% after 6 iterations
- Quality: 5.5/10
- Time: 6 passes (120 papers, 18 memo writes)

### After (Target):
- Coding skew: <45% (max 9/20 code papers)
- Coverage: ≥70% within 4 iterations
- Quality: ≥8/10
- Time: ≤4 passes (80 papers, 8 memo writes)

### Improvement:
- 36% reduction in coding skew
- 35% improvement in coverage
- 45% improvement in quality
- 33% reduction in iterations
- 55% reduction in memo writes

---

## VII. ROLLBACK PLAN

Nếu fixes gây regression:

1. **Git**: Each fix in separate commit
   ```bash
   git log --oneline
   git revert <commit-hash>
   ```

2. **Feature flags** (optional):
   ```python
   ENABLE_DOMAIN_BALANCING = os.getenv("ENABLE_DOMAIN_BALANCING", "true") == "true"
   ENABLE_QUALITY_FILTER = os.getenv("ENABLE_QUALITY_FILTER", "true") == "true"
   ```

3. **A/B testing** (optional):
   - Run both old and new paths
   - Compare quality metrics
   - Choose better path

---

## VIII. KHÔNG LÀM (Out of Scope)

❌ **Conceptual verification** - Quá phức tạp, cần LLM judge riêng
❌ **Factuality pipeline** - Cần infrastructure mới
❌ **UI changes** - Backend fixes only
❌ **New dependencies** - Use existing libraries only
❌ **Database schema changes** - Not needed for these fixes

---

## IX. NEXT STEPS

1. Review plan với user
2. Implement Phase 1 (Domain balance + Followup specificity)
3. Run tests, verify no regressions
4. Implement Phase 2 (Quality loop filter)
5. E2E test với real query
6. Commit, push, create PR

Estimated time: 2-3 hours (careful implementation + testing)
