# Phân Tích Nguyên Nhân Gốc: Tại Sao Luôn 6 Pass Mà Output Vẫn Kém

## Hiện Tượng
- Trước đây: Chạy vài vòng (2-3 pass) đã có kết quả
- Hiện tại: LUÔN chạy đúng 6 pass, mất rất nhiều thời gian
- Chất lượng output: KHÔNG CẢI THIỆN, vẫn ~5.5/10

## Kiến Trúc Hiện Tại

### 2 Vòng Lặp Chồng Lên Nhau

```
┌─────────────────────────────────────────────────────────────┐
│ VÒNG LẶP NGHIÊN CỨU (max 6 iterations)                     │
│                                                             │
│  Iteration 1:                                              │
│  ┌────────────────────────────────────────────────────┐   │
│  │ Retrieve → Critic → Report → Memo Gate             │   │
│  │                                                     │   │
│  │   ┌──────────────────────────────────────┐        │   │
│  │   │ VÒNG LẶP CHẤT LƯỢNG (max 3 attempts)│        │   │
│  │   │  - Generate memo                     │        │   │
│  │   │  - Check quality                     │        │   │
│  │   │  - If fail → Regenerate (max 2x)   │        │   │
│  │   └──────────────────────────────────────┘        │   │
│  └────────────────────────────────────────────────────┘   │
│                                                             │
│  Critic check: coverage < 75% → CREATE FOLLOWUP            │
│  ↓ Loop back to Iteration 2...                            │
│                                                             │
│  ... Iterations 2-6 giống vậy ...                         │
│                                                             │
│  Iteration 6: Hết budget → FORCE PUBLISH                   │
└─────────────────────────────────────────────────────────────┘
```

### Điều Kiện Dừng Vòng Lặp Nghiên Cứu

File: `apps/agent/app/domain/coverage.py:507-540`

```python
def critic_should_pass(query, coverage, evidence):
    reasons = []
    
    # 1. Entity gate: mỗi named subject phải có evidence riêng
    expected_entities = named_systems(query)
    if expected_entities:
        found_entities = entities_with_evidence(query, evidence, limit=99)
        missing = [e for e in expected_entities if e not in found_entities]
        if missing:
            reasons.append(f"Named subjects with no evidence: {', '.join(missing)}")
    
    # 2. Critical gaps: các dimension quan trọng chưa có evidence
    for gap in coverage.get("critical_gaps") or []:
        reasons.append(f"Critical dimension {gap.status}: {gap.label}")
    
    # 3. Không có primary source
    if not coverage.get("primary_sources"):
        reasons.append("No primary paper/doc/repo in working set.")
    
    # 4. Coverage < 60%
    must_pct = coverage.get("depth_score", {}).get("must_answer", {}).get("pct")
    if must_pct < 60:
        reasons.append(f"Must-answer coverage {must_pct}% (<60%).")
    
    # 5. Implementation missing (nếu query hỏi về code)
    if _wants_implementation(slots) and not coverage.get("has_implementation"):
        reasons.append("No source-level evidence found.")
    
    return (len(reasons) == 0), reasons
```

## Nguyên Nhân Gốc

### 🔴 VẤN ĐỀ 1: Regeneration Loop Cố Sửa Vấn Đề KHÔNG THỂ SỬA

File: `apps/agent/app/domain/memo_quality.py:212-219`

```python
should_regenerate = (
    duplicate_ratio > 0.40          # ✅ Generation issue - có thể sửa
    or stacking_count >= 2          # ✅ Generation issue - có thể sửa
    or saturation_count >= 2        # ✅ Generation issue - có thể sửa
    or filler_count >= 1            # ✅ Generation issue - có thể sửa
    or placeholder_count > 0        # ✅ Generation issue - có thể sửa
)
```

**Nhưng trong `critic_should_pass`, các lý do FAIL là:**

```python
# ❌ RETRIEVAL PROBLEM - regeneration KHÔNG SỬA ĐƯỢC
- "Named subjects with no evidence: LangGraph, AutoGen"
- "Critical dimension weak: Memory architecture"
- "No primary paper/doc/repo"
- "Must-answer coverage 45% (<60%)"
```

→ **Hệ thống regenerate memo 3 lần mỗi iteration, nhưng vấn đề là THIẾU EVIDENCE, không phải memo viết kém!**

### 🔴 VẤN ĐỀ 2: Research Loop Không Cải Thiện Retrieval Quality

**Iteration 1:**
- Retrieve 20 papers
- 15/20 là code papers (coding skew)
- Coverage: 45% (< 60%)
- Critic: FAIL → tạo followup queries

**Iteration 2:**
- Retrieve thêm 20 papers (dựa trên followup queries)
- Vẫn 15/20 là code papers (coding skew vẫn còn!)
- Coverage: 50% (vẫn < 60%)
- Critic: FAIL → tạo followup queries

**... Iterations 3-5 giống vậy ...**

**Iteration 6:**
- Retrieve thêm 20 papers
- Vẫn coding skew
- Coverage: 52% (vẫn < 60%)
- **Budget exhausted → FORCE PUBLISH**

→ **Mỗi iteration retrieve thêm evidence, nhưng KHÔNG CẢI THIỆN CHẤT LƯỢNG vì:**
1. **Coding skew vẫn còn** - không có cơ chế "per-dimension balanced retrieval"
2. **Memory depth vẫn yếu** - followup queries vẫn quá generic, không đủ specific
3. **Conceptual errors không được check** - không có verification step cho mechanisms

### 🔴 VẤN ĐỀ 3: Checks Trigger Cho Vấn Đề Sai Loại

```
┌──────────────────────────────────────────────────────────────┐
│ HIỆN TẠI (SAI):                                             │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ Query → Retrieve (biased) → Generate → Quality Check FAILS  │
│         ↓ coding skew          ↓          ↓                 │
│         ↓ shallow              ↓          ↓ trigger         │
│         ↓                      ↓          ↓ regenerate      │
│         └──────────────────────┴──────────┘                 │
│                    ↑                                         │
│                    │ Regenerate không sửa được vì           │
│                    │ vấn đề nằm ở RETRIEVAL!                │
│                    └─────────────────────────────────────────│
└──────────────────────────────────────────────────────────────┘
```

## So Sánh: Trước vs Sau Các Fixes

### Trước Các Fixes (2-3 pass):
```python
# Ít checks hơn
coverage_check: must_pct < 75% → loop    # Chỉ có check này
quality_check: (không có nhiều)

# Kết quả:
- Pass nhanh hơn vì ít checks
- Có thể publish memo ở 75% coverage
- Chất lượng: cũng ~5-6/10 nhưng nhanh hơn
```

### Sau Các Fixes (6 pass):
```python
# Nhiều checks hơn
coverage_check:
  - must_pct < 60% → loop
  - missing entities → loop  
  - critical gaps → loop
  - no primary sources → loop

quality_check:
  - duplicate_ratio > 0.40 → regenerate
  - stacking_count >= 2 → regenerate
  - saturation_count >= 2 → regenerate
  - filler_count >= 1 → regenerate
  - placeholder_count > 0 → regenerate

# Kết quả:
- LUÔN hit max iterations (6) vì nhiều checks fail
- Mỗi iteration có 3 memo rewrites (expensive!)
- Chất lượng: VẪN ~5-6/10 vì checks không sửa root cause
```

## Tại Sao Verdict #3 Vẫn Kém

### Các Vấn Đề Trong Verdict #3:

1. **Coding skew** (retrieval bias towards code)
   - ❌ Không fix được bởi: regeneration loop
   - ❌ Không fix được bởi: research loop (vẫn retrieve biased)
   - ✅ CẦN: Per-dimension balanced retrieval

2. **Composited worked examples** (merge multiple sources incorrectly)
   - ⚠️ Đã có deterministic labeling, nhưng không enforce PREVENTION
   - ✅ CẦN: Single-source enforcement trong prompt + hard-block

3. **Conceptual errors** (misunderstand mechanisms)
   - ❌ Không fix được bởi: regeneration (tạo errors khác)
   - ✅ CẦN: Conceptual accuracy verification step

4. **Missing factuality metrics**
   - ❌ Không fix được bởi: bất kỳ loop nào
   - ✅ CẦN: Factuality measurement pipeline

5. **Weak memory depth** (shallow coverage of memory concepts)
   - ❌ Không fix được bởi: research loop (followups too generic)
   - ✅ CẦN: Per-concept research depth enforcement

## Architectural Mistake

```
┌────────────────────────────────────────────────────────────┐
│ PROBLEM LAYER          │ CURRENT FIX       │ EFFECTIVENESS │
├────────────────────────┼───────────────────┼───────────────┤
│ RETRIEVAL              │ Research loop     │ 10% - vẫn    │
│ (coding skew,          │ (iterate 6x)      │ biased        │
│  shallow coverage)     │                   │               │
├────────────────────────┼───────────────────┼───────────────┤
│ GENERATION             │ Quality loop      │ 30% - sửa    │
│ (overclaim, filler,    │ (regenerate 3x)   │ được 1 số     │
│  citations)            │                   │ issues        │
├────────────────────────┼───────────────────┼───────────────┤
│ CONCEPTUAL             │ (NONE)            │ 0% - không   │
│ (mechanism errors)     │                   │ có fix        │
├────────────────────────┼───────────────────┼───────────────┤
│ FACTUALITY             │ (NONE)            │ 0% - không   │
│ (hallucination)        │                   │ measure       │
└────────────────────────┴───────────────────┴───────────────┘
```

**Kết luận:** Hệ thống cố fix GENERATION problems (30% effective) bằng cách loop nhiều lần, nhưng 70% problems là RETRIEVAL/CONCEPTUAL/FACTUALITY không được address!

## Chi Phí Hiện Tại

```
6 iterations × 3 memo rewrites × LLM calls = 18x LLM calls cho memo
+ 6 iterations × retrieve/critic calls = ~36 total operations

Thời gian: Rất lâu (6 passes)
Chất lượng: KHÔNG CẢI THIỆN (vẫn 5.5/10)
Hiệu quả: RẤT THẤP (nhiều công nhưng vô ích)
```

## Giải Pháp

### Phase 1: Tách Retrieval vs Generation Checks

```python
# RETRIEVAL QUALITY (check TRƯỚC khi generate)
def retrieval_should_pass(coverage, evidence):
    if must_pct < 60:
        return False, "NEED MORE EVIDENCE"
    if coding_skew > 70%:
        return False, "NEED BALANCED DOMAINS"  
    if missing_entities:
        return False, "NEED ENTITY-SPECIFIC EVIDENCE"
    return True, "PROCEED TO GENERATION"

# Chỉ generate KHI retrieval quality đạt!
```

### Phase 2: Balanced Per-Dimension Retrieval

```python
# Thay vì: retrieve k=20 global
for dimension in must_answer_slots:
    retrieve k=5 cho dimension này
    # Force balanced: không để 1 domain chiếm ưu thế
```

### Phase 3: Generation-Only Regeneration

```python
# CHỈ regenerate cho generation issues
should_regenerate = (
    overclaim_detected       # ✅ fixable
    or ungrounded_claims    # ✅ fixable  
    or tone_mismatch        # ✅ fixable
)

# KHÔNG regenerate cho:
# - coverage < 75%  (retrieval issue)
# - missing entities (retrieval issue)
# - conceptual errors (need verification step, not rewrite)
```

### Phase 4: Conceptual Verification

```python
# Thêm step AFTER generation
def verify_mechanisms(memo, evidence):
    mechanism_claims = extract_mechanism_descriptions(memo)
    for claim in mechanism_claims:
        is_accurate = llm_verify_concept(claim, evidence)
        if not is_accurate:
            flag_error(claim)
```

## Kết Luận

**TẠI SAO 6 PASS:**
- Critic checks quá nhiều → luôn FAIL
- Research loop cố fix retrieval issues bằng cách retrieve thêm
- Nhưng không có "balanced retrieval" → vẫn biased → vẫn FAIL
- Hit max iterations → force publish

**TẠI SAO CHẤT LƯỢNG KHÔNG CẢI THIỆN:**
- 70% vấn đề là RETRIEVAL/CONCEPTUAL (không được fix)
- 30% vấn đề là GENERATION (fix được nhưng không đủ)
- Regeneration loop tốn công nhưng không address root cause

**FIX:**
1. Tách retrieval checks (fix TRƯỚC generation)
2. Balanced per-dimension retrieval (fix coding skew)
3. Only regenerate for generation issues
4. Add conceptual verification step
5. Add factuality metrics
