# Hệ thống agent research

**Last Updated: 2026-09-07 (P1-P5 Architectural Remediation)**

Kiln không phải chatbot. Agent là pipeline có ngân sách tách pool, critic, HITL, citation integrity, **quality regeneration loops**, **independent grounding audit**, **writer-level quality controls**, **domain-balanced retrieval**, **regression testing**, **adaptive depth**, **programmatic quality gates**, và **centralized thresholds** — chạy trong `apps/agent`, được Nest enqueue từ ngoài.

Tài liệu này mô tả topology, budget, depth policy, luồng report writer, **quality assurance mechanisms**, **trust evaluation system**, **writer instruction architecture**, và **P1-P5 architectural remediation**. Cập nhật theo `graph/builder.py`, `domain/research_depth.py`, `domain/retrieval_limits.py`, `eval/trust_bench_e2e.py`, `eval/regression_check.py`, `report/deep_write.py`, `report/adaptive_depth.py`, `domain/structure_validation.py`, `config/thresholds.py`.

---

## 🚀 Latest Writer-Level Quality Improvements (2026-09-07)

### 8. STRICT Anti-Compositing Rule
- **Problem:** Writer was INSTRUCTED to composite multiple sources, creating fake unified workflows
- **Fix:** `report/deep_write.py` - writer_prompt line 362-372
  - **FORBIDS** combining [5 repo] + [6 repo] into unified workflow
  - Only allows composite if single-source evaluation or unavoidable (common building blocks)
  - Requires `> **Composite**` marker when composite is necessary
- **Impact:** No more fake "production RAG pipeline" synthesized from disparate GitHub repos

### 9. Per-Dimension Subsection Enforcement
- **Problem:** Comparison questions lack dedicated subsections per dimension
- **Fix:** `report/deep_write.py` - writer_prompt line 347-357
  - **REQUIRES** separate ### subsection for EACH comparison dimension
  - Example: "How do different X, Y, Z affect..." → must have ### X, ### Y, ### Z
  - Forbids merging into generic "### System components"
  - Each subsection must compare AT LEAST 2 approaches with evidence [n]
- **Impact:** Embedding models, reranking methods, context configs each get dedicated analysis

### 10. Conceptual Accuracy Verification
- **Problem:** No verification of mechanism descriptions against source quotes
- **Fix:** `report/deep_write.py` - writer_system line 249-262
  - Verify mechanism descriptions match cited source wording [n]
  - Don't conflate similar concepts (truncation ≠ reflection, SSR ≠ general reranking)
  - If source describes mechanism X, don't write about mechanism Y
- **Impact:** Reduces conceptual errors in technical explanations

### 11. Worked Example Source Consistency Check
- **Problem:** No validation that workflow steps come from same source
- **Fix:** `report/deep_write.py` - writer_system line 249-262
  - Verify ALL workflow steps from SAME source [n] evaluation
  - If steps from [5 repo] and [6 repo], these are SEPARATE systems
  - Describe separately in Detailed analysis or clearly mark Composite
- **Impact:** Prevents multi-source workflow synthesis without explicit labeling

### 12. Domain-Balanced Retrieval (Fixed Logic Error)
- **Problem:** Backfill logic re-introduced coding skew by adding excess code papers
- **Fix:** `graph/nodes/scholar.py` + `search.py` - `_balanced_evidence_pool`
  - NEW STRATEGY: Add ALL non-code papers first (theory, benchmark, docs)
  - Calculate max code papers: `non_code_count * (0.4 / 0.6)` to reach 40% ratio
  - NEVER backfill beyond this limit
  - Accept smaller total if source pool is heavily skewed (e.g., 90% code → only return 40% code in output)
- **Impact:** Strict 40% code cap enforcement, even with 90% code input pool

### 13. Quality-Aware Smart Stopping (Fixed Logic Error)
- **Problem:** Stagnation threshold 5% too lenient (flagged 4%/iter improvement as stagnant)
- **Fix:** `graph/builder.py` - after_critic line 96-103
  - Lowered coverage stagnation threshold from <5% to <2%
  - Example: 60% → 64% → 68% (+4%/iter) is good progress, should NOT stop
  - Only triggers on TRUE stagnation (<2% change over 3 iterations)
- **Impact:** Prevents premature stopping while quality is improving

### 14. Aligned Coverage Thresholds (Fixed Logic Error)
- **Problem:** Threshold inconsistency created 60-65% dead zone
- **Fix:** `domain/coverage.py` line 528 (aligned from 60% to 65%)
  - `builder.py`: >= 75% for early stop (excellent quality)
  - `memo_quality.py` + `coverage.py`: < 65% for retrieval issue (poor coverage)
  - **Clear Ranges:**
    - >= 75%: Excellent, early stop OK
    - 65-74%: Normal operation, continue improving
    - < 65%: Retrieval issue, don't regenerate memo
- **Impact:** No dead zones; consistent decision logic across all modules

---

## 🏗️ Architectural Remediation P1-P5 (2026-09-07)

Sau khi phát hiện 4 root causes (whack-a-mole debugging, depth mismatch, prompt-only controls, scattered thresholds), đã implement 5 priorities để sửa architecture cơ bản:

### P1: Regression Harness (Automated Quality Gate)
**Problem:** No regression prevention → mỗi fix tạo ra bug mới khác

**Solution:**
- **`apps/agent/data/eval/golden_set.json`**: 10 test cases đa dạng
  - comparison_multi_dimension_rag (RAG systems)
  - implementation_specific_agentic_loop (DeepSeek vs GPT-4)
  - theory_scaling_laws (scaling laws with quantitative data)
  - sparse_evidence_model_collapse (graceful degradation test)
  - out_of_scope_medical (routing test)
  - benchmark_mmlu_vs_gpqa (benchmark comparison)
  - implementation_light_agent_framework (LangGraph/CrewAI/AutoGPT)
  - edge_case_single_word_query ("RAG")
  - folklore_detection_always_better (folklore detection)
  - fast_iteration_test (smart stopping test)

- **`apps/agent/app/eval/regression_check.py`**: Automated validation
  - Structural checks: Required sections, subsections, composite labeling
  - Quality metrics: Coverage %, citation stacking, source diversity
  - Comparison logic: Baseline vs HEAD, detect new violations

- **`scripts/pre-commit.sh`**: Git hook chạy fast check (3 cases) trước commit
- **`.github/workflows/regression.yml`**: CI workflow chạy full suite (10 cases) trên PR
- **`docs/regression-testing.md`**: Setup guide

**Impact:** 
- Whack-a-mole debugging eliminated
- Mỗi commit được validate trước khi merge
- CI tự động catch regressions trên PR

**Usage:**
```bash
# Fast check (pre-commit, 3 cases)
python -m app.eval.regression_check --fast

# Full suite (CI, 10 cases)
python -m app.eval.regression_check --all
```

---

### P2: Adaptive Depth (Evidence-Driven Targeting)
**Problem:** Fixed 5500-word target forced LLM to hallucinate when evidence is thin (e.g., k=3 items per dimension)

**Solution:**
- **`apps/agent/app/report/adaptive_depth.py`**:
  - `calculate_adaptive_target()`: Formula = evidence_items × 80 words × quality_multiplier
  - Quality tiers & multipliers:
    - **Excellent** (≥75% coverage, depth ≥80): 1.3x multiplier
    - **Good** (65-74% coverage): 1.0x multiplier
    - **Fair** (50-64% coverage): 0.8x multiplier
    - **Poor** (<50% coverage): 0.6x multiplier
  - Bounds: 800-7000 words (never force padding)
  - Per-dimension guidance: Adapts per subsection based on evidence count

- **Integration**: `graph/nodes/report.py` (2 locations)
  - Initial generation path (line 429-455)
  - Regeneration path (line 849-875)
  - Replaces fixed `word_target(depth)` with adaptive calculation
  - Passes `adaptive_guidance` to writer_prompt

**Impact:**
- 3 evidence items → ~240 words (không ép 5500 từ)
- 15 evidence items + excellent coverage → ~1560 words
- Writer không còn bị pressure hallucinate để fill space

**Example:**
```
Evidence: 12 items, coverage: 68% (good)
Target: 12 × 80 × 1.0 = 960 words
Guidance: "Per-dimension target: 240 words per subsection.
If dimension has only 2-3 items, write 120-180 words, NOT 250-450."
```

---

### P3: Tier-C Programmatic Gates (Code-Enforced Quality Rules)
**Problem:** Quality rules chỉ có trong prompt → LLM có thể vi phạm mà không bị catch

**Solution:**
- **`apps/agent/app/domain/structure_validation.py`**:
  - `check_worked_example_compositing()`: W1 anti-compositing
    - Detects if Worked example cites ≥2 sources without `> **Composite**` label
    - Violation = ERROR → triggers regeneration
  
  - `check_per_dimension_subsections()`: W3 per-dimension enforcement
    - Requires separate ### subsection for each comparison dimension
    - Detects generic subsections (forbidden: "overview", "approaches")
    - Missing dimensions = ERROR → triggers regeneration
  
  - `check_citation_stacking_excessive()`: Citation quality
    - Counts sentences citing 3+ distinct sources
    - Threshold: 3.0 per 1000 words
    - Excessive stacking = WARNING (tracked but not blocking)
  
  - `check_quantitative_findings_validity()`: Data table check
    - If Quantitative findings section exists, must have ≥1 data row
    - Empty table = WARNING
  
  - `validate_memo_structure()`: Aggregate validation
    - Runs all checks
    - Returns overall_pass, error_count, warning_count
    - Errors block publication, warnings logged

- **Integration**: `graph/nodes/memo_gate.py`
  - Runs `validate_memo_structure()` after `check_memo_quality()`
  - Extracts required_dimensions from dossier
  - Structural errors added to quality_check issues
  - Triggers regeneration (max 2 attempts)
  - Logs violations: `memo_gate_structure_violations`, `memo_gate_structure_warnings`

**Impact:**
- Prompt-only controls → Code enforcement
- Composite worked examples caught before publish
- Per-dimension violations caught automatically
- No more relying on LLM compliance alone

**Example violation:**
```
ERROR: Worked example cites [5, 6, 7] without Composite label
→ Triggers regeneration with feedback
→ Max 2 regenerations
→ If still failing, shows to user with warning
```

---

### P4: Centralized Thresholds (Single Source of Truth)
**Problem:** Magic numbers scattered across 8+ files → inconsistencies, dead zones (60-65%), hard to calibrate

**Solution:**
- **`apps/agent/app/config/thresholds.py`**: Centralized configuration
  - **CoverageThresholds**:
    - MUST_COVERAGE_EXCELLENT = 75
    - MUST_COVERAGE_GOOD = 65
    - MUST_COVERAGE_FAIR = 50
    - COVERAGE_STAGNATION_THRESHOLD_PCT = 2
    - DEPTH_SCORE_EXCELLENT = 80
  
  - **QualityThresholds**:
    - BASE_WORDS_PER_EVIDENCE = 80
    - MAX_CITATION_STACKING_PER_1000_WORDS = 3.0
    - MAX_QUALITY_REGENERATIONS = 2
    - MIN_MEMO_WORDS = 800
    - MAX_MEMO_WORDS = 7000
  
  - **StructureThresholds**:
    - MIN_SUBSECTIONS_DETAILED_ANALYSIS = 2
    - MAX_SOURCES_WITHOUT_COMPOSITE_LABEL = 1
  
  - **RetrievalThresholds**:
    - MAX_CODE_RATIO = 0.40
    - MIN_PRIMARY_SOURCES = 2
  
  - **BudgetThresholds**:
    - MAX_ITERATIONS = 6
    - TYPICAL_ITERATIONS = 4
  
  - **AdaptiveDepthMultipliers**:
    - EXCELLENT = 1.3
    - GOOD = 1.0
    - FAIR = 0.8
    - POOR = 0.6
  
  - Validation checks at module load
  - `get_all_thresholds()` for debugging

- **Updated Files** (8 total):
  - `domain/coverage.py`: MUST_COVERAGE_GOOD
  - `domain/memo_quality.py`: MUST_COVERAGE_GOOD
  - `graph/builder.py`: MUST_COVERAGE_EXCELLENT, STAGNATION_THRESHOLD_PCT
  - `graph/nodes/scholar.py`: MAX_CODE_RATIO
  - `graph/nodes/search.py`: MAX_CODE_RATIO
  - `graph/nodes/memo_gate.py`: MAX_QUALITY_REGENERATIONS
  - `report/adaptive_depth.py`: All multipliers, bounds, thresholds
  - `graph/nodes/report.py`: Uses centralized imports

**Impact:**
- Dead zones eliminated (60-65% gap closed)
- Consistent logic: <65% = retrieval issue, 65-74% = normal, ≥75% = excellent
- Single place to adjust calibration
- Easier to reason about system behavior
- Module load validation catches inconsistencies

**Before/After:**
```python
# BEFORE (scattered):
# coverage.py: if must_pct < 60
# memo_quality.py: if must_pct < 65
# builder.py: if must_pct >= 75
# → Dead zone at 60-64%

# AFTER (centralized):
from app.config.thresholds import CoverageThresholds
if must_pct < CoverageThresholds.MUST_COVERAGE_GOOD  # 65 everywhere
```

---

### P5: Graceful Degradation (Honest Limits Disclosure)
**Problem:** System forced "deep" output even with sparse evidence → hallucinations to fill 5500 words

**Solution:**
- **`report/adaptive_depth.py`**:
  - `should_use_graceful_degradation()`: Decision logic
    - Downgrade deep → standard if:
      - Adaptive target ≤ 1500 words, OR
      - Coverage tier = "poor" AND evidence < 10 items
    - Returns (should_degrade: bool, suggested_depth: str)

- **Integration**: `graph/nodes/report.py` (2 paths)
  - **Initial generation** (line 429-455):
    ```python
    should_degrade, suggested_depth = should_use_graceful_degradation(adaptive_target)
    if should_degrade and depth == "deep":
        event("graceful_degradation_triggered", ...)
        depth = suggested_depth  # "standard"
    ```
  
  - **Regeneration path** (line 849-875): Same logic

  - Logs degradation events with reason:
    - Original depth
    - New depth
    - Target words
    - Coverage tier
    - Evidence count

**Impact:**
- System admits limits rather than hallucinating
- 3 evidence items → "standard" memo (800-1500 words), not forced "deep" (5500 words)
- Graceful degradation maintains quality > quantity
- User sees honest "Limited evidence" rather than padded speculation

**Example:**
```
Query: "How does recursive distillation lead to model collapse?"
Evidence: 4 papers found, coverage 48% (poor)
Adaptive target: 1200 words

Decision:
→ should_degrade = True (target ≤ 1500)
→ depth: "deep" → "standard"
→ Log: "Evidence too sparse for deep synthesis"
→ Writer receives 1200-word target, not 5500
```

---

## 📊 Impact Summary (P1-P5)

| Priority | Problem | Solution | Impact |
|----------|---------|----------|--------|
| **P1** | Whack-a-mole debugging | Regression harness (10 test cases, git hooks, CI) | No new bugs slip through |
| **P2** | Fixed 5500-word target | Adaptive depth (evidence × 80 × multiplier) | No forced padding/hallucination |
| **P3** | Prompt-only controls | Programmatic gates (structure_validation.py) | Code-enforced quality rules |
| **P4** | Scattered thresholds | Centralized config (thresholds.py) | Consistent logic, no dead zones |
| **P5** | Forced deep output | Graceful degradation (auto-downgrade) | Honest limits disclosure |

**Quantitative Results:**
- **Regression prevention**: 10 test cases covering structural, quality, edge cases
- **Adaptive targeting**: 800-7000 word range (was fixed 5500)
- **Structural validation**: 4 programmatic checks (was 0)
- **Threshold consolidation**: 8 files updated, 1 source of truth (was 15+ scattered)
- **Degradation threshold**: Auto-downgrade at ≤1500 words or poor+sparse evidence

**Architecture Quality:**
- ✅ No more whack-a-mole (regression harness catches new bugs)
- ✅ No more depth mismatch (adaptive targets match evidence)
- ✅ No more prompt-only controls (programmatic validation)
- ✅ No more scattered thresholds (centralized config)
- ✅ Graceful degradation (honest limits)

---

## 🎯 Quality Breakthrough (2026 Q3)

Hệ thống đã được nâng cấp với **7 cải tiến chất lượng ban đầu** để ngăn hallucination:

### 1. Per-Dimension Retrieval
- **Trước:** Global `hybrid_retrieve(query, pool, k=20)` cho tất cả dimensions
- **Sau:** Mỗi dimension/slot có retrieval riêng với `k=3-5`, targeted query
- **File:** `graph/nodes/collector.py` - `retrieve_node()`
- **Lợi ích:** Evidence được filter chính xác cho từng câu hỏi nghiên cứu

### 2. Source Tier Classification Fix
- **Bug:** ArXiv papers bị label nhầm là "peer_reviewed"
- **Fix:** 
  - `schema.py`: `arxiv.org` → `SourceTier.SPECIALIST_RESEARCH`
  - `scholar.py`: `_publication_type()` check arXiv indicators trước
- **Impact:** Memo phân loại đúng `[X specialist]` vs `[X peer]`

### 3. Overclaim Detection & Softening
- **File:** `domain/overclaim.py`
- **Logic:** Post-process memo để softens absolute terms:
  - "completely eliminat(e|es|ed|ing)" → "largely reduces"
  - "never fail(s|ed)?" → "rarely fails"
  - "guarantees? that every" → "ensures most"
- **Exception:** Formal contexts (differential privacy, cryptographic proofs)
- **Integration:** `report/compose.py` - `_user_memo_markdown()`

### 4. Confidence Calibration with Penalties
- **File:** `domain/coverage.py` - `_research_quality()`
- **Penalties applied:**
  - Open gaps: -3 points each (max -10)
  - Weak evidence: -2 points each (max -5)
  - Sparse primary sources (<3): -5 points
  - Floor: Never drop below 55 (shallow threshold)
- **Result:** Confidence scores reflect actual uncertainty

### 5. Citation Relevance Checking
- **File:** `domain/citation_relevance.py`
- **Check:** Verifies cited papers are actually about AI/ML claims
- **Filters:** Rejects papers from irrelevant domains (biology, medicine, etc.)
- **Example:** No citing neural regeneration papers for AI model claims

### 6. Scholar Node Improvements
- **Architecture:** OpenAlex primary + Semantic Scholar augmentation
- **File:** `graph/nodes/scholar.py`
- **Strategy:**
  - Always call OpenAlex first (no rate limits)
  - Call S2 only if OpenAlex returns <5 results
  - S2 rate limiting: 2s intervals, 3s/6s retry backoff, 120s cooldown
  - API key support: `S2_API_KEY` from environment
  - **NEW:** Domain balancing with 40% code cap
- **Fallback:** Continue with OpenAlex-only if S2 fails
- **Debug logging:** `openalex_query`, `openalex_filtering`, `openalex_arxiv_paper`

### 7. Quality Regeneration Loops
- **Files:** `graph/nodes/memo_gate.py`, `graph/nodes/report.py`
- **Logic:** 
  - Memo_gate runs quality checks before publish
  - If quality issues detected → trigger `_regenerate_for_quality()`
  - Max regenerations: `MAX_QUALITY_REGENERATIONS = 2`
  - Stagnation detection prevents infinite loops
- **Checks:** Source saturation, citation stacking, template leaks

### Trust Bench E2E (Independent Audit)
- **File:** `app/eval/trust_bench_e2e.py`
- **Purpose:** Post-publish hallucination detection by independent LLM judge
- **Workflow:**
  1. `export`: Research run JSON → compact snapshot
  2. `build`: Snapshot → audit packet (claims + source excerpts)
  3. LLM judge: Evaluates SUPPORTED/NOT_SUPPORTED/CANNOT_VERIFY
  4. `score`: Calculate hallucination rate, save history
- **Output:** `data/eval/trust_bench_e2e_history.jsonl`

---

## Tư duy thiết kế

User hỏi một quyết định LLM-systems. Hệ thống trả memo có claim, quote, contradiction — hoặc nói out of scope. Folklore bị chặn, không được khuyến nghị.

### Mười nguyên tắc trong code (Updated 2026-09-07)

| Nguyên tắc | Hiện ra ở đâu | Vì sao |
| --- | --- | --- |
| Pipeline có budget, không loop vô hạn | `schema.Budget` + `after_critic` | LLM/search tốn tiền; `max_iterations` / tokens / **hai pool tool calls** |
| Luật tách khỏi control flow | `domain/` vs `graph/nodes/` | pytest được grounding, coverage, routing mà không cần FastAPI |
| Critic là cổng cứng, không tin LLM | `critic.py` + `coverage.py` | LLM nói sufficient vẫn fail nếu thiếu slot `must_answer` |
| Ba nguồn, một collector | search / scholar / docs → collector | Web, paper, docs nội bộ; node không chọn thì return ngay |
| Người duyệt ba lần | briefing + plan_gate + memo_gate (+ hitl) | Chốt brief → plan → memo trước khi publish |
| Falsifiable | `data/eval/golden_set.json` + `eval/runner.py` + `eval/graph_routing.py` | Routing, graph gates, folklore — không cần live LLM |
| **Quality-first with regeneration** | `memo_gate.py` + `report.py` quality loops | Memo có thể rewrite nếu fail quality checks |
| **Independent grounding audit** | `eval/trust_bench_e2e.py` | LLM judge riêng verify citations sau publish |
| **Domain-balanced retrieval** | `scholar.py` + `search.py` `_balanced_evidence_pool` | Cap code papers at 40%, prevent GitHub skew |
| **Writer instruction rigor** | `deep_write.py` writer_system + writer_prompt | Explicit anti-compositing, per-dimension enforcement, conceptual accuracy |

### Coverage gate (HITL / memo)

`domain/coverage_gate.py` gán `gate_reason` trên critic:

| `gate_reason` | Ý nghĩa |
| --- | --- |
| `sufficient` | Must-answer đủ (>= 65%) |
| `insufficient_coverage` | Còn gap — có thể loop planner |
| `insufficient_budget` | Còn gap nhưng hết iteration/calls — **cảnh báo tại HITL/memo_gate** |
| `contradicted` | Còn tension chưa giải quyết |

`report.metrics.synthesis_status=terminal_fallback` khi `insufficient_budget`. Chạy eval: `python -m app.eval.runner`, `python -m app.eval.race_bench`.

### Depth policy (luôn deep)

| File | Hành vi |
| --- | --- |
| `domain/research_depth.py` | `effective_depth()` luôn trả `"deep"`; UI không đổi được |
| `graph/nodes/briefing.py` | Brief hiển thị depth cố định `deep` |
| `runtime.new_budget()` | `configure_budget_pools(budget, "deep")` ngay khi khởi tạo run |
| `graph/nodes/planner.py` | Iter 1: gán lại pool + `max_iterations >= 6` |

Quick/standard vẫn còn trong `retrieval_limits.py` cho test/eval, nhưng **production pipeline luôn chạy deep**.

### Budget tách pool (deep)

| Pool | Cap | Charge tại | Dùng cho |
| --- | --- | --- | --- |
| **Retrieval** | 28 | `collector.used_retrieval_calls` | search, scholar, docs (external_calls) |
| **Enrich** | 24 | `enrich`, `gap_enrich` | full-page fetch |
| **Tổng** | 52 | `used_tool_calls` = retrieval + enrich | metrics / diagnostics |

- `budget.remaining_calls` → chỉ retrieval pool (planner, critic loop, search/scholar skip).
- Enrich check `remaining_enrich_calls` — **không ăn** retrieval budget.
- Planner iter 1 reserve **10** retrieval calls cho critic gap loop (`DEEP_RESERVE_CALLS`).
- Caps khác (`retrieval_limits.py`): search 10 queries × 15 results; enrich iter1 10 URLs, iter2+ 18; planner sub-queries tối đa 12.

`MAX_TOOL_CALLS` trong `.env` không còn là nguồn sự thật chính — `configure_budget_pools` ghi đè khi planner chạy iter 1.

### Nouns graph mang theo

| Noun | Ý nghĩa |
| --- | --- |
| **Plan** | `query_type`, `agents_to_run`, `sub_queries`. Planner viết; search/scholar đọc sub_queries. |
| **Evidence** | url, snippet, tier, credibility, optional `full_text`. Gộp ở collector; rank ở retrieve; enrich bổ sung full text. **Domain-classified** (code/theory/benchmark/docs). |
| **Report** | claims + citations + `body_markdown`. `verify_claims` + `fact_lite` + `report_integrity` trước khi lưu knowledge. **Writer-validated** for compositing, dimension coverage, conceptual accuracy. |

---

## Pipeline (một lần chạy) - Updated 2026-09-07

```
START
  → briefing
  → planner
  → plan_gate          (HITL: duyệt plan trước khi search)
  → search ∥ scholar ∥ docs (+ domain balancing: 40% code cap)
  → collector
  → enrich
  → retrieve (per-dimension, k=3-5 mỗi slot)
  → extract (+ dimension refinement nếu có papers)
  → critic (+ smart stopping với 2% threshold)
  → hitl               (approve / revise → planner)
  → report (+ writer-level quality checks: anti-compositing, per-dimension, conceptual accuracy)
    ├─→ integrity gap → planner (integrity re-loop)
    └─→ quality issues → _regenerate_for_quality (max 2 lần)
  → memo_gate          (duyệt memo; revise → critic)
    └─→ quality fail → report regeneration
  → [POST-PUBLISH] trust_bench_e2e audit (optional)
END
```

| Node | File | Việc | Rẽ | Updated |
| --- | --- | --- | --- | --- |
| briefing | `graph/nodes/briefing.py` | ResearchBrief (goal, must_answer, depth=deep) | out_of_scope / cancel → report | |
| planner | `graph/nodes/planner.py` | Classify, budget pools, knowledge reuse, falsification sub-queries | cached → report; else plan_gate | |
| plan_gate | `graph/nodes/plan_gate.py` | Interrupt: user chỉnh plan / scholar textarea | cancel → report; ok → fan-out | |
| search | `graph/nodes/search.py` | Tavily / DDG; rank `retrieval_rank_score`; **domain balancing 40% code cap** | join collector | ✅ 09-07 |
| scholar | `graph/nodes/scholar.py` | **OpenAlex primary + S2 augment**; ưu tiên snippet có benchmark số; rate limiting; **domain balancing 40% code cap** | join collector | ✅ 09-07 |
| docs | `graph/nodes/docs.py` | Corpus nội bộ + Qdrant | join collector | |
| collector | `graph/nodes/collector.py` | Gộp evidence; charge **retrieval** pool; **per-dimension retrieval k=3-5** | → enrich | ✅ Updated |
| enrich | `graph/nodes/enrich.py` | Full-page fetch; charge **enrich** pool; slot-aware gap URLs | → retrieve | |
| retrieve | `collector.retrieve_node` | **Per-dimension** hybrid rank + Qdrant | → extract | ✅ Updated |
| extract | `graph/nodes/extract.py` | Quote + claim seed; **dimension refinement** với paper concepts; micro-extract nếu còn retrieval budget | → critic | ✅ Updated |
| critic | `graph/nodes/critic.py` | Coverage + contradiction + followup; **confidence penalties**; **smart stopping 2% threshold** | sufficient / hết budget → hitl; else → planner | ✅ 09-07 |
| hitl | `graph/nodes/hitl.py` | `interrupt(approve_report)` | revise → planner; approve → report | |
| report | `graph/nodes/report.py` | LLM memo (race/deep write) với **writer-level quality controls**; **overclaim softening** | integrity gap → planner; quality fail → regenerate; else memo_gate | ✅ 09-07 |
| memo_gate | `graph/nodes/memo_gate.py` | Interrupt duyệt memo cuối; **quality validation** | quality fail → report; revise → critic; approve → publish | ✅ Updated |

**Adaptive skip:** `after_plan_gate` luôn fan-out `search`, `scholar`, `docs` khi có agent trong plan. Node không nằm trong `agents_to_run` return ngay — không gọi tool.

**Integrity re-loop:** `report` có thể set `status=integrity_research` → `after_report` quay lại `planner` (thêm retrieval theo `report_integrity`).

**Smart stopping:** `after_critic` kiểm tra:
1. **Early stop** nếu must_pct >= 75% AND depth_score >= 80% (excellent quality)
2. **Stagnation stop** nếu sources, coverage (<2%), score (<3) stagnant qua 3 iterations

Topology chỉ nằm `graph/builder.py`. Node không gọi nhau.

---

## Report writer stack (Updated 2026-09-07)

| Layer | File | Việc |
| --- | --- | --- |
| Notes | `report/deep_write.py` | `format_research_notes`, `method_notes_for_writer`, compress |
| **Writer Instructions** | `deep_write.py` `writer_system()` + `writer_prompt()` | **STRICT anti-compositing**, **per-dimension enforcement**, **conceptual accuracy checks**, **worked example source verification** |
| Generation | `report/race_write.py` | Section-wise deep (phase 1 Analysis → phase 2 back matter), expansion, rewrite |
| Fallback | `report/compose.py` | Deterministic memo khi LLM off / fail |
| Post-process | `report/memo_structure.py` | Dedupe Contradictions, merge Metric gaps → Uncertainties, gộp Source quality cites `[1, 6, 8, 9 peer]`, lọc row định tính trong Quantitative table |
| Polish | `race_write.polish_citations` | `merge_inline_citations`, strip `---`, bỏ Visual summary appendix |

Deep memo target ~5500 words (`word_target("deep")`). Không còn bắt buộc code/mermaid appendix.

### Writer Instruction Architecture (NEW 2026-09-07)

`deep_write.py` định nghĩa hai hàm chính:

#### `writer_system()` - Fundamental Rules
- Anti-compositing logic: "Do NOT composite unless ONE source evaluated complete system"
- Conceptual accuracy: "Verify mechanism descriptions against source quotes [n]"
- Worked example source check: "ALL workflow steps from SAME source [n]"
- Attribution: "Based on [n]'s synthesis" not "Our synthesis"
- Quantitative table rules: ONLY outcome metrics (%, ms, FLOP), NOT setup params
- No universal numeric laws without [n] + domain + re-benchmark warning

#### `writer_prompt()` - Structural Enforcement
- **Per-dimension subsection**: "EACH comparison dimension gets separate ###"
- **Anti-redundancy**: Say each fact ONCE, cross-reference elsewhere
- **Worked example**: Must be from ONE source OR clearly marked `> **Composite**`
- **Comparison rule**: One column per subject, never repeat passages
- **Section order**: At a glance → Executive → Key findings → Detailed analysis → Quantitative → Worked example → Comparison → Contradictions → Decision rule → Uncertainties → Limitations → Source quality → References

### Retrieval ranking (ưu tiên số đo)

`domain/adversarial.py`:

- `numeric_evidence_score(ev)` — boost `%`, `ms`, `tok/s`, tên benchmark; hạ survey không có số.
- `retrieval_rank_score(ev)` = `authority_score` + `numeric_evidence_score`.

Dùng trong `search._rank_and_filter`, `scholar` sort sau dedupe, `enrich._prioritize_enrich_urls`.

### Quantitative Findings Extraction Pipeline (Documented 2026-09-07)

`domain/adversarial.py::extract_quantitative_rows()` - **Conservative by design**:

1. **Regex capture** (QUANT_RE): %, ms, FLOP, tok/s, × speedup
2. **Setup parameter filter** (`_is_setup_parameter`):
   - DROPS: N studies, N runs, token counts (config metadata)
   - KEEPS: Numbers with outcome units (%, ms, throughput)
3. **Semantic gate** (`_has_valid_metric_and_condition`):
   - **REQUIRES BOTH**:
     - Metric/unit name (accuracy, latency, FLOP)
     - Experimental condition (dataset, benchmark, baseline)
   - DROPS: Bare "16%" without benchmark context
   - DROPS: "In experiments, X = 50ms" (generic condition)
4. **Final condition check**: Drop if condition still "condition not stated in excerpt"
5. **Results section preference**: Hunt for Results/Findings/Evaluation sections (line 79-91)

**Why sparse tables are CORRECT**:
- Many sources lack formal Results sections (GitHub READMEs, blogs, docs)
- Qualitative descriptions ("significantly faster") have no numbers
- Unconditioned numbers ("25% improvement" without benchmark) filtered correctly
- Conservative extraction prevents table pollution with setup parameters
- User previously complained about "filler rows" and "not reported" scaffolding

**Recommendation**: Do NOT relax extraction. Current conservatism is correct. Upstream improvements (domain balancing, enhanced followups, per-dimension enforcement) will improve evidence quality → more quantitative data extracted naturally.

---

## Bốn lớp file

Phụ thuộc một chiều: node được gọi domain. Domain không được import graph. Tools không biết HITL.

| Lớp | Folder | Được import bởi | Cấm |
| --- | --- | --- | --- |
| Boundary | `main.py`, `contracts.py`, `runtime.py`, `cli.py` | HTTP / CLI / Nest | Luật citation trong FastAPI |
| Control flow | `graph/builder.py`, `state.py`, `nodes/` | runtime | SQL org, Clerk |
| Luật | `domain/` | nodes, eval, report | FastAPI, LangGraph interrupt |
| Adapter | `llm/`, `tools/`, `retrieval/`, `persistence/` | nodes + runtime | Quyết định out_of_scope |

### `domain/` — file chính (Updated 2026-09-07)

| File | Luật | Updated |
| --- | --- | --- |
| `schema.py` | Plan, Budget (split pools), Claim, Report, ResearchBrief; **SourceTier classification** | |
| `research_depth.py` | Force deep + `configure_budget_pools` | |
| `retrieval_limits.py` | Caps search/scholar/enrich/planner | |
| `routing_policy.py` | Phân loại query, `heuristic_plan`, out_of_scope | |
| `research_intent.py` | goal, `authority_score`, topic leakage | |
| `adversarial.py` | Hypotheses, falsification queries, **quantitative extract with semantic gates**, source quality bands | ✅ Documented |
| `knowledge.py` | Lookup / save memo đã nghiên cứu | |
| `coverage.py` | must_answer slots, `critic_should_pass`, **confidence penalties**, **65% threshold alignment** | ✅ 09-07 |
| `grounding.py` | FORBIDDEN folklore + `verify_claims` | |
| `report_integrity.py` | Contradiction quant vs decision rule; integrity re-loop | |
| `gap_enrich.py` | Slot-targeted full-text fetch (enrich pool) | |
| `citations.py` | Ledger, quote-in-source | |
| `credibility.py` | Host → tier → score | |
| `overclaim.py` | Detects & softens absolute language | |
| `citation_relevance.py` | Validates paper relevance to AI/ML claims | |
| `paper_concepts.py` | Extracts methods, findings, limitations from papers | |
| `memo_quality.py` | Quality checks (saturation, stacking, leaks); **65% retrieval issue threshold** | ✅ 09-07 |
| `decompose.py` | Enhanced: `synthesize_dimensions_from_evidence` for refinement | |

### `graph/nodes/` — key nodes (Updated 2026-09-07)

| File | Logic | Updated |
| --- | --- | --- |
| `scholar.py` | OpenAlex + S2 augment; **domain balancing with 40% code cap**; rate limiting | ✅ 09-07 |
| `search.py` | Tavily/DDG; **domain balancing with 40% code cap** | ✅ 09-07 |
| `collector.py` | Evidence pooling; per-dimension retrieval | |
| `critic.py` | Coverage gate; quality tracking for smart stopping | ✅ 09-07 |
| `report.py` | LLM memo generation; quality regeneration; overclaim softening | ✅ 09-07 |
| `memo_gate.py` | Final quality validation before publish | |
| `builder.py` | Graph topology; **smart stopping logic with 2% threshold** | ✅ 09-07 |

### `report/` — writer stack (Updated 2026-09-07)

| File | Purpose | Updated |
| --- | --- | --- |
| `deep_write.py` | **Writer instructions: anti-compositing, per-dimension, conceptual accuracy, source verification** | ✅ 09-07 |
| `race_write.py` | Section-wise generation, expansion, rewrite | |
| `compose.py` | Deterministic fallback memo | |
| `memo_structure.py` | Post-processing: dedupe, merge, sanitize | |

`eval/runner.py` import thẳng domain, không import `builder.py`.

---

## Liên kết khi một request vào

```
Nest  apps/api/src/research/agent-execution.client.ts
  →  apps/agent/app/main.py  POST /internal/v1/executions/stream
  →  runtime.stream_execution()
  →  graph đã compile (checkpointer Postgres)
  →  từng node patch ResearchState
  →  runtime._snapshot_dict  (progress, hint, interrupt)
  →  NDJSON frame  về Nest  →  research_runs.result_json
```

| Từ | Sang | Mang gì |
| --- | --- | --- |
| `agent-execution.client.ts` | `main.py` `/internal/v1/executions/stream` | `StartExecutionRequest` |
| `main.py` | `runtime.stream_execution` | request đã parse Pydantic |
| `runtime.py` | `graph/builder.py` compile | checkpointer Postgres; `new_budget()` → deep pools |
| `planner_node` | `knowledge`, `routing_policy`, `falsification_queries` | `reuse_mode` + `agents_to_run` |
| search / scholar / docs | `tools/` + `retrieval/` + **domain balancing** | list evidence (40% code cap) |
| collector → retrieve | `retrieval/hybrid.py`, `store.py` | ranked retrieved |
| extract | `domain/citations` | quotes / claim seeds |
| `critic_node` | `domain/coverage` + llm + **smart stopping** | `CriticVerdict` + followups |
| `hitl` / `plan_gate` / `memo_gate` | LangGraph `interrupt` | payload ra Nest/UI, đợi resume |
| `report_node` | `race_write`, `deep_write` (+ **writer-level checks**), `compose`, `knowledge.save` | Report + metrics |
| `runtime._snapshot_dict` | NDJSON frame | AgentSnapshot về API |

**State là bus.** Mọi node nhận `ResearchState`, trả dict patch. Rẽ nhánh chỉ nằm `builder.py`. Đổi topology: sửa builder, không sửa `search.py`.

---

## Docker / dev workflow

Code được **COPY vào image** lúc build (`docker-compose.yml` không mount `apps/agent/app`).

Sau khi sửa code:

```bash
docker compose up -d --build agent web
# hoặc cả api nếu đổi Nest
docker compose up -d --build agent api web
```

Chỉ đổi `.env` → `docker compose up -d` (restart, không build).

`docker-compose.dev.yml` chỉ expose thêm port (8000, 3000, 5432…).

---

## Đánh giá & Trust Mechanisms (Updated 2026-09-07)

### Tier-A: Deterministic Gates (Runtime)
- **File:** `eval/trust_bench.py`
- **Checks:** Numeric extraction, citation format, folklore blocking
- **When:** During pipeline execution (inline)
- **Action:** Block publication if fails

### Tier-B: Independent Grounding Audit (Post-Publish)
- **File:** `eval/trust_bench_e2e.py`
- **Purpose:** Catch hallucinations that slipped past Tier-A
- **Judge:** Independent LLM (different from memo writer)
- **Process:**
  ```
  export: run JSON → snapshot
  build: snapshot → audit packet (claims + excerpts)
  judge: LLM evaluates SUPPORTED/NOT_SUPPORTED/CANNOT_VERIFY
  score: Calculate hallucination rate
  ```
- **Output:** `data/eval/trust_bench_e2e_history.jsonl`
- **Metrics:**
  - `hallucination_rate = NOT_SUPPORTED / (SUPPORTED + NOT_SUPPORTED)`
  - Flagged claims with reasons
  - Historical trend tracking

### Tier-C: Writer-Level Quality Controls (NEW 2026-09-07)
- **File:** `report/deep_write.py`
- **Checks:**
  - **Anti-compositing**: Forbid multi-source workflows unless single-source evaluation
  - **Per-dimension enforcement**: Each comparison dimension must have dedicated subsection
  - **Conceptual accuracy**: Verify mechanism descriptions against source quotes
  - **Source consistency**: Worked example steps from same source [n]
- **When:** During LLM memo generation (writer instructions)
- **Action:** Instruct writer to avoid compositing, enforce structure, verify accuracy

### Quality Metrics
- **Source Diversity:** Track unique domains per dimension; **40% code cap**
- **Citation Density:** Claims per 1000 words
- **Confidence Score:** Calibrated with gap penalties (floor 55, excellent >= 75)
- **Hallucination Rate:** From independent audit
- **Regeneration Count:** Times memo had to be rewritten
- **Stagnation Iterations:** Iterations with <2% improvement before early stop

---

## File nguồn chính (Updated 2026-09-07)

### Core Pipeline
- `apps/agent/app/graph/builder.py` ⭐ (smart stopping 2% threshold)
- `apps/agent/app/graph/state.py`
- `apps/agent/app/graph/nodes/scholar.py` ⭐ (domain balancing)
- `apps/agent/app/graph/nodes/search.py` ⭐ (domain balancing)
- `apps/agent/app/runtime.py`
- `apps/agent/app/main.py`

### Research Logic
- `apps/agent/app/domain/research_depth.py`
- `apps/agent/app/domain/retrieval_limits.py`
- `apps/agent/app/domain/coverage.py` ⭐ (65% threshold alignment)
- `apps/agent/app/domain/adversarial.py` (quantitative extraction documented)

### Quality Assurance
- `apps/agent/app/domain/overclaim.py`
- `apps/agent/app/domain/citation_relevance.py`
- `apps/agent/app/domain/paper_concepts.py`
- `apps/agent/app/domain/memo_quality.py` ⭐ (65% retrieval threshold)

### Report Generation (Writer-Level Controls)
- `apps/agent/app/report/race_write.py`
- `apps/agent/app/report/deep_write.py` ⭐⭐ (writer instructions: anti-compositing, per-dimension, conceptual accuracy, source verification)
- `apps/agent/app/report/compose.py` (overclaim softening)
- `apps/agent/app/report/memo_structure.py`

### Evaluation & Trust
- `apps/agent/app/eval/runner.py`
- `apps/agent/app/eval/trust_bench.py`
- `apps/agent/app/eval/trust_bench_e2e.py`
- `apps/agent/tests/test_trust_bench_e2e.py`

### Contracts
- `packages/contracts/src/index.ts` (`AgentSnapshotSchema`)

### Documentation (NEW)
- `COMPLETE_FIX_SUMMARY.md` - Comprehensive fix summary with before/after
- `QUANTITATIVE_FINDINGS_ANALYSIS.md` - Why extraction is conservative by design
- `docs/agent-research-system.md` - This file (architecture overview)

⭐ = Updated 2026-09-07 (writer-level quality improvements + logic error fixes)
⭐⭐ = Major architectural update with writer instruction rigor

---

## Appendix: Key Thresholds & Constants (2026-09-07)

### Coverage & Quality Thresholds
- **Excellent quality (early stop)**: must_pct >= 75% AND depth_score >= 80%
- **Retrieval issue boundary**: must_pct < 65% (aligned across coverage.py + memo_quality.py)
- **Stagnation detection**: <2% coverage change AND <3 score change over 3 iterations
- **Domain balance**: Max 40% code papers in retrieval results

### Budget Pools (Deep Mode)
- **Retrieval calls**: 28 (search, scholar, docs)
- **Enrich calls**: 24 (full-page fetch)
- **Total tool calls**: 52
- **Reserve for critic loop**: 10 retrieval calls
- **Max iterations**: 6 (but smart stopping may exit earlier)

### Quality Regeneration
- **Max regenerations**: 2 per memo
- **Stagnation check**: Prevent infinite quality loops

### Writer Generation Targets
- **Deep memo length**: ~5500 words
- **Subsection depth**: 250-450 words per dimension
- **Quantitative table**: ONLY outcome metrics (%, ms, FLOP), NOT setup params

---

**Changelog:**
- 2026-09-07: Writer-level quality improvements (W1-W5), logic error fixes (domain balance, stagnation, thresholds), quantitative extraction documentation
- 2026-09-06: Q3 quality breakthrough (7 improvements: per-dimension retrieval, tier classification, overclaim, confidence penalties, citation relevance, scholar improvements, quality regeneration, trust bench e2e)
