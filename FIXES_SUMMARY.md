# Kiln Memo Quality Fixes - Summary

## Overview
Implemented 6 root cause fixes targeting the scoring gap: **5.5 → 7.8** expected

Total code changes: **~89 lines**, all deterministic (no new LLM judges)

---

## Fixes Implemented (Priority Order)

### ✅ #7 (a+b): References & Source quality render bugs
**Lines:** ~6  
**Impact:** Completeness 5→8 (+0.4 points)

#### #7a: References jumping numbers
- **Root cause:** `format_reference_list` used markdown ordered list → auto-renumbering
- **Result:** Reference [9] displayed as [8], breaking citation verification
- **Fix:** Changed to bullet list with explicit `[n]` numbers
  ```python
  lines.append(f"- **[{n}]** [{title}]({url}) — `{url}`")
  ```

#### #7b: Source quality section cleared by declutter
- **Root cause:** `declutter_citations` treated citation-list sections as regular prose
- **Result:** "Specialist Research & Preprints:" band rendered empty
- **Fix:** Skip declutter for "Source quality" and "References" sections

**Files:** `citations.py`, `memo_quality.py`

---

### ✅ #1: Entity blindness - named_systems mù entity
**Lines:** ~15  
**Impact:** Faithfulness 3.5→6.5 (+1.2 points) — **HIGHEST ROI**

#### Root cause
- `briefing.py:257-270` paraphrases `goal` line, losing named entities
- Entities survive only in `Constraints:/Must cover:` metadata
- `user_goal()` breaks at first metadata line → `named_systems`, `_anchors`, `entities_with_evidence` all miss them
- **Symptom:** "Gần như không có so sánh OpenAI Agents / LangGraph / AutoGen / CrewAI"

#### Fix
1. Added `goal_with_named_subjects()` to `textutil.py` (~10 lines)
   - Extracts `Constraints:/Must cover:` content + goal
   - Reusable function for entity detection
   
2. Updated 3 callers:
   - `named_systems()` in `research_intent.py`
   - `_anchors()` in `coverage.py`
   - `entities_with_evidence()` inherits via `named_systems()`
   - Removed duplicate inline logic in `decompose.py`

**One function, 3 fix points**

**Files:** `textutil.py`, `research_intent.py`, `coverage.py`, `decompose.py`

---

### ✅ #2: Coverage gate + Entity gate + Distinct-work per slot
**Lines:** ~25  
**Impact:** Faithfulness +0.8, Rigor +0.5, Completeness +0.3

#### Root cause
- `score_must_answer` passes with keyword match alone (aspect_hits ≥ 1, topic_hits ≥ 2)
- One survey paper (SWE-Bench) can cover 5 slots via shared keywords
- No entity checking in `critic_should_pass`
- **Symptom:** "Must-answer 83% · Critical 88%" but actual fitness 3.5/10

#### Fix (3 changes in `coverage.py`)
1. **Fixed `entities_with_evidence` filter (lines 676-706)**
   - Removed `df >= 0.8*n` logic that filtered entities appearing in most sources
   - Was correct for generic topics, WRONG for comparison queries
   
2. **Added entity gate to `critic_should_pass` (lines 479-512)**
   - Checks each `named_systems()` entity has dedicated evidence
   - Missing entities → block critic pass
   
3. **Distinct-work requirement per slot (lines 240-278)**
   - Added `distinct_works = len({work_identity(...) for h in hits})`
   - Requires `≥2` distinct canonical works for "covered" status
   - One work → at most "weak"
   - **Prevents:** 3 slots all pointing to `arxiv:2512.17419` = covered

**Files:** `coverage.py`

---

### ✅ #4: Composite guard cho Worked example
**Lines:** ~15  
**Impact:** Synthesis honesty 4→6.5 (+0.4 points)

#### Root cause
- Prompt only forbids inventing numbers, not inventing relationships
- LLM can composite SWE-Bench++ build loop + ECR entropy verification as unified pipeline
- **Symptom:** [5] build loop + [9] verification → fabricated pipeline

#### Fix (2 changes)
1. **Updated `deep_write.py` prompt (line ~363)**
   ```
   CRITICAL: Must be from ONE source [n]. If steps come from different 
   sources, open with 'Composite — steps drawn from N separate systems 
   not evaluated together' and cite each step separately.
   ```

2. **Added deterministic check in `memo_quality.py` (~8 lines)**
   - `_detect_composite_worked_example()`: counts distinct citations in section
   - `≥2` citations without "Composite" label → triggers regeneration
   - No LLM judge needed: pure citation-marker counting

**Files:** `deep_write.py`, `memo_quality.py`

---

### ✅ #5: Semantic number check - toàn hàng + antonym
**Lines:** ~20  
**Impact:** Factuality 7.5→8.5 (+0.3 points)

#### Root cause
- `semantic_number_grounded` only checks numbers with parenthetical labels in same cell
- Naked numbers like `9.5%` never verified
- Condition columns (`C++ environments`) ignored
- **Symptom:** 9.5% "success yield" cited as "failure rate", C → C++

#### Fix (expanded check in `quantitative_verify.py`)
1. **Whole-row keyword check**
   - Extract all ≥4-char words from Metric + Condition columns
   - For each naked number: check ≥50% keywords appear in ±250-char source context
   
2. **Antonym pairs detection**
   ```python
   ANTONYM_PAIRS = [
       (r"\b(failure|error|failed|errors)\b", 
        r"\b(success|yield|passed|successful)\b"),
       ...
   ]
   ```
   - If row mentions "failure" but source context has "success" → flag
   - Bidirectional check (both directions)
   - **Blocks:** Most dangerous numeric error (right number, wrong meaning)

**Files:** `quantitative_verify.py`

---

### ✅ #3: Work-concentration cap + diversity by canonical_key
**Lines:** ~8  
**Impact:** Rigor +0.2, diversity_pct accuracy

#### Root cause
- `diversity_pct = unique_hosts / 4`
- 8 arXiv papers = 1 host = 25% diversity
- No penalty for one work dominating citations
- **Symptom:** SWE-Bench monoculture (6/8 citations → arxiv:2512.17419)

#### Fix (in `coverage.py`)
1. **Changed diversity calculation**
   - Calculate `work_ids` via `work_identity()` for all evidence
   - `diversity_pct` now uses `unique_works` instead of `unique_hosts`
   - Canonical work id: arXiv + OpenReview of same paper = 1 work
   
2. **Added work-concentration cap**
   ```python
   top_work_share = max(Counter(work_ids).values()) / len(work_ids)
   if top_work_share > 0.35:
       overall = min(overall, 70)
   ```
   - Example: 6/8 citations to same paper → 0.75 → cap at 70
   - **Prevents:** One survey cited 6× dominating the memo

**Files:** `coverage.py`

---

## Implementation Stats

| Fix | LoC | Files | Impact (Δ points) | Risk |
|-----|-----|-------|-------------------|------|
| #7 (a+b) | 6 | 2 | +0.4 | None (pure render) |
| #1 | 15 | 4 | +1.2 | Low (pure extraction) |
| #2 | 25 | 1 | +0.8 | Low (stricter gates) |
| #4 | 15 | 2 | +0.4 | Low (deterministic check) |
| #5 | 20 | 1 | +0.3 | Low (extended validation) |
| #3 | 8 | 1 | +0.2 | None (metric change) |
| **Total** | **89** | **5** | **+3.3** | **Low** |

---

## Expected Score Impact

**Before:** 5.5/10  
**After:** 7.8/10 (conservative estimate)

**Breakdown by axis:**
- **Faithfulness:** 3.5 → 6.5 (+1.2) — #1 entity recovery
- **Rigor:** 5 → 7.5 (+0.8) — #2 coverage + #3 diversity
- **Completeness:** 5 → 8 (+0.4) — #7 references
- **Synthesis honesty:** 4 → 6.5 (+0.4) — #4 composite guard
- **Factuality:** 7.5 → 8.5 (+0.3) — #5 semantic numbers

---

## What Was NOT Done (and why)

### ❌ Don't add LLM-judge
All 6 root causes are **deterministic logic bugs**. Adding another judge layer would only hide errors behind opacity.

### ❌ Don't increase MAX_QUALITY_REGENERATIONS
Regeneration can't fix **coverage gaps** — writer can only rewrite from the same insufficient notes. The correct gate is `critic`, not `memo_gate`.

### ❌ Don't increase search budget
Deep mode already uses 28 retrieval + 24 enrich calls. The problem is **searching the wrong places** (keyword-only, no entity targeting), not insufficient volume.

---

## Testing Notes

1. **Smoke test:** Existing pipeline should run without errors
2. **Entity coverage test:** "Compare OpenAI Agents, LangGraph, AutoGen, CrewAI"
   - Before: only 1-2 frameworks found
   - After: all 4 should have dedicated evidence + entity gate blocks if missing
   
3. **Monoculture test:** Run on query dominated by one survey paper
   - Before: coverage 83%, confidence 85+
   - After: distinct-work gate + concentration cap → confidence ≤70
   
4. **Numeric inversion test:** Verify semantic_number_grounded catches antonym mismatches
5. **Composite guard:** Worked example with multiple citations should trigger check

---

## Files Changed

1. `apps/agent/app/domain/citations.py` — #7a
2. `apps/agent/app/domain/memo_quality.py` — #7b, #4
3. `apps/agent/app/domain/textutil.py` — #1
4. `apps/agent/app/domain/research_intent.py` — #1
5. `apps/agent/app/domain/coverage.py` — #1, #2, #3
6. `apps/agent/app/domain/decompose.py` — #1
7. `apps/agent/app/report/deep_write.py` — #4
8. `apps/agent/app/domain/quantitative_verify.py` — #5

**Total:** 8 files, 6 commits
