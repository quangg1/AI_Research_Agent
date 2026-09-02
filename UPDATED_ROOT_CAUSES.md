# Updated Root Cause Analysis - Verified Against Local Tree

## Pipeline (Verified)
briefing → planner → plan_gate → (search || scholar || docs) → collector → enrich → **retrieve** → extract → critic → (hitl | report) → memo_gate

## Confirmed ROOT Causes

### 1. Global Top-K Reused for All Dimensions ⚠️ HIGHEST PRIORITY
**Location**: `apps/agent/app/graph/nodes/collector.py:retrieve_node` lines 72-79

```python
ranked = hybrid_retrieve(
    query,
    pool,
    k=min(RETRIEVE_TOP_K, max(8, len(pool))),  # RETRIEVE_TOP_K = 20
    use_llm_reranker=llm.available,
)
```

**Issue**: ONE hybrid_retrieve call returns global top-20. Then:
- `coverage.score_must_answer` / `build_evidence_dossier` regex-score that SAME pool
- Evidence assigned to dimensions is from this global pool (hits[:3] per dimension)
- `compose._analysis_sections` uses query-level anchors
- `_relevant_sentences` accepts if `aspect==0 but topic_terms hit` → same quotes in every dimension

**Fix Required**: After extract/critic identifies dimensions with `score_must_answer`, do per-dimension retrieval:
- For each must-answer dimension, rerank the pool by dimension-specific patterns/terms
- Assign top evidence per dimension (not global top-k)
- Prevent same evidence from dominating multiple unrelated dimensions

### 2. First PDF Chunk Becomes Quote ⚠️ HIGH PRIORITY  
**Location**: `apps/agent/app/tools/fetch.py:evidence_from_url` lines 168-169

```python
"snippet": text[:1600],
"quote": text[:500],  # First 500 chars = title/keywords page
```

**Issue**: 
- PDFs concatenated into single `text` blob
- First 500 chars (title, keywords, authors, [[page 1]]) become the quote
- `citations.pick_quote` uses `ev.quote or snippet or full_text or title` → defaults to title page
- No intra-document retrieval; `locator.locate_quote` only locates an already-chosen quote

**Fix Required**: 
- Chunk fetched PDFs like corpus notes (retrieval/chunk.py: 900-word windows, one row per chunk)
- When selecting evidence for a claim/dimension, pick highest-similarity span
- Currently: my `passage.py` does this but it's applied too late (at compose time)
- Need: Apply at fetch time or at evidence selection time

### 3. Numeric Extraction Lacks Gates ✅ FIXED
**Location**: `apps/agent/app/domain/adversarial.py`
- `QUANT_RE` first alt matches any `\d+(?:\.\d+)?%`
- `extract_quantitative_rows` sets `metric_name=token` (the raw number)
- `_is_setup_parameter` KEEPS anything with `%`
- `_metric_condition` defaults to "condition not stated in excerpt"

**Fix Applied**: Added `_has_valid_metric_and_condition()` gate requiring BOTH metric/unit AND experimental condition

### 4. Critic Doesn't Block Memo Publication ✅ FIXED
**Location**: `apps/agent/app/graph/nodes/memo_gate.py`
- Critic runs BEFORE report
- After report, `memo_gate_node` only loops to critic on HITL `revise_critic`
- `memo_gate_node_auto` just publishes
- Compose uses critic text for Limitations, not regeneration

**Fix Applied**: Added `check_memo_quality()` to detect issues and auto-regenerate

### 5. Hardcoded Filler ✅ FIXED
**Location**: `apps/agent/app/report/compose.py` line 515
```python
blocks.append(f"**{dim.get('label')}** is carried by the collected sources. {cites}".strip())
```

**Fix Applied**: Removed filler text

### 6. quantitative_verify.py Behavior (LOCAL ONLY - NOT ON ORIGIN/MAIN)
**Status**: File does NOT exist on origin/main  
**Behavior to preserve**: `sanitize_quantitative_table` drops untraceable rows; empty table → "not traceable to the cited evidence (likely model confabulation)"

**Action**: Numeric semantic gate already drops untraceable numbers; confabulation message preserved in compose.py

## Updated Implementation Plan

### Priority 1: Fix Per-Dimension Retrieval at Source
**Target**: `collector.py:retrieve_node` or new step after extract/critic
**Approach**:
1. Keep current `retrieve_node` as-is (returns global top-20)
2. After `extract_node` when dimensions are identified, add dimension-specific reranking
3. Update `coverage.build_evidence_dossier` to use per-dimension scores, not global pool

### Priority 2: Fix PDF Chunking at Source  
**Target**: `fetch.py:evidence_from_url` or evidence selection in coverage/compose
**Approach**:
1. Option A: Chunk PDFs at fetch time (like corpus notes)
2. Option B: Keep current fetch, but select best chunk per dimension in `build_evidence_dossier`
3. My `passage.py` module can be reused for chunk selection logic

### Status
- ✅ Fixes 3, 4, 5 already implemented correctly
- ⚠️ Fixes 1, 2 need to target the ROOT location (collector.py, fetch.py or coverage.py)
- Current passage.py/compose.py changes are PARTIAL - they help but don't fix the root
