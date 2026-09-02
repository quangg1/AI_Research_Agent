# Kiln Memo Quality Fixes - Root Cause Analysis

## Verified Root Causes

### 1. Hardcoded Filler Text (CONFIRMED)
**Location**: `apps/agent/app/report/compose.py:515`
```python
blocks.append(f"**{dim.get('label')}** is carried by the collected sources. {cites}".strip())
```
**Issue**: When a dimension has evidence but synthesis is empty, this generic filler is emitted. Matches symptom 1 exactly: "is carried by the collected sources" appears in the buggy memo.

### 2. Chunk Selection at Wrong Layer (CONFIRMED)
**Locations**:
- `apps/agent/app/domain/citations.py:pick_quote` (lines 87-108)
- `apps/agent/app/report/compose.py:_relevant_sentences` (lines 342-367)

**Issue**: 
- `pick_quote` doesn't rerank passages by similarity to a specific claim - it just takes the first available content (quote → snippet → full_text → title) and truncates
- No intra-document passage retrieval/reranking means title pages and keyword lists become the "best" excerpt
- `_relevant_sentences` scores sentences but works on already-selected text, not on choosing the best chunk from a document

### 3. Numeric Extraction Lacks Semantic Gate (CONFIRMED)
**Location**: `apps/agent/app/domain/adversarial.py`
- Lines 64-73: `QUANT_RE` pattern matches bare years, generic tokens
- Lines 423-452: `_is_setup_parameter` tries to filter but doesn't enforce required gates
- Lines 462-475: `_metric_condition` returns "condition not stated in excerpt" as fallback
- Lines 372-420: `extract_quantitative_rows` builds rows without requiring metric+unit+condition

**Issue**:
- Regex matches "1970s", "16%", "53%" without context
- Creates "Unverified numeric mentions" table with junk like "condition not stated in excerpt"
- No requirement that a number have both a metric/unit name AND an experimental condition

### 4. Critic Doesn't Feedback into Regenerate (CONFIRMED)
**Locations**:
- `apps/agent/app/graph/nodes/critic.py` - computes findings
- `apps/agent/app/graph/nodes/memo_gate.py` - only checks human decision

**Issue**:
- Critic computes quality signals but only sets status/reasons
- memo_gate only checks for human interrupt decision (action == "revise_critic")
- No automatic thresholds for:
  - Duplicate quotes across sections (symptom 1: same 2 quotes in 3 subsections)
  - Citation stacking (3+ sources on one sentence)
  - Empty filler sentences
- Critic findings go to Limitations, not to a regenerate trigger

### 5. Section Routing Per Dimension (PARTIALLY CONFIRMED)
**Location**: `apps/agent/app/report/compose.py:_analysis_sections` (line 369)

**Issue**:
- Evidence IS grouped per dimension via `dim.get("items")`
- Evidence assignment happens in `apps/agent/app/domain/coverage.py:build_evidence_dossier` (lines 536-578)
- HOWEVER: Evidence is from a global pool and assigned once; no per-dimension reranking at synthesis time
- The same global top-k evidence can end up in multiple dimensions if it pattern-matches multiple slots

### 6. Template Fill (NOT FOUND IN CODE)
**Status**: "REVISIT IF: when" placeholder mentioned in symptom 7 not found in codebase search
**Likely cause**: Dynamic rendering or formatting issue, not a static template

## Implementation Plan

### Priority 1: Chunk-Level Retrieval (Largest Impact)
Add passage-level reranking function that scores chunks within a document by similarity to a specific claim/dimension. Use in pick_quote and _relevant_sentences.

### Priority 2: Route Evidence Per Dimension
Before synthesis, rerank each dimension's evidence by relevance to that specific dimension's patterns/topic_terms. Don't reuse global top-k.

### Priority 3: Semantic Gate on Numeric Extraction
Modify extract_quantitative_rows to require:
- Metric/unit name in matched token OR nearby context
- Experimental condition (dataset, setup, benchmark) in nearby context
- Drop numbers that fail both gates; remove "Unverified numeric mentions" fallback table

### Priority 4: Wire Critic into Memo Gate
Add quality thresholds to memo_gate:
- Detect duplicate quotes across sections (>40% overlap in multiple dimension synthesises)
- Detect citation stacking (3+ distinct sources in one sentence)
- Detect empty filler (exact match on "is carried by the collected sources")
- Trigger regenerate instead of just logging to Limitations

### Priority 5: Validate Template Fill
Add validation pass before publishing that checks for literal placeholders, unresolved [n], dangling field values.

### Priority 6: Preserve Existing Guardrails
Keep and strengthen:
- Confabulation guardrail for numeric tables (already exists)
- Decision-rule format distinguishing empirical cutoffs from AI heuristics (already good)
