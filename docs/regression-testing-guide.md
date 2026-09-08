# 🧪 Hướng Dẫn Chạy Regression Tests

## 📋 Tổng Quan

Regression test system gồm 3 cấp độ:
1. **Fast check** (3 test cases) - Chạy trước mỗi commit (pre-commit hook)
2. **Full suite** (15 test cases) - Chạy trên CI (GitHub Actions)
3. **Manual run** - Chạy thủ công để debug hoặc validate

---

## 1️⃣ Setup Môi Trường

### Bước 1: Cài đặt dependencies
```bash
cd /workspace
pip install -r apps/agent/requirements.txt
```

### Bước 2: Cấu hình .env
Đảm bảo các API keys cần thiết đã được cấu hình trong `.env`:
```bash
# OpenAI for LLM and embeddings
OPENAI_API_KEY=sk-...

# Semantic Scholar for retrieval
SEMANTIC_SCHOLAR_API_KEY=...

# (Optional) OpenAlex backup
OPENALEX_EMAIL=...
```

### Bước 3: Start database (nếu cần)
```bash
docker compose up -d db redis
```

---

## 2️⃣ Chạy Regression Tests

### Option A: Fast Check (3 test cases, ~2-3 phút)
```bash
cd /workspace
python -m app.eval.regression_check --fast
```

**Khi nào dùng:**
- Trước khi commit code
- Quick validation sau khi sửa logic
- CI build nhanh

**Output:**
```
✓ PASS: comparison_rag_hallucination
✓ PASS: implementation_agentic
✗ FAIL: theory_foundations
  - Expected must_pct >= 65, got 58
  - Missing dimension: theoretical_proofs
```

### Option B: Full Suite (15 test cases, ~10-15 phút)
```bash
cd /workspace
python -m app.eval.regression_check
```

**Khi nào dùng:**
- Trước khi merge PR
- Sau khi implement architectural changes
- Weekly validation

**Output:**
```
Running 15 regression tests...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ 12 passed
✗ 2 failed
⊘ 1 skipped
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Failed tests:
1. benchmark_quantitative
   - Expected: quantitative_findings section present
   - Got: Section missing (no data rows)
   
2. sparse_evidence_graceful_degrade
   - Expected: word_count <= 3500 (adaptive)
   - Got: 5200 words (fixed depth policy triggered)
```

### Option C: Single Test Case (debug mode)
```bash
cd /workspace
python -m app.eval.regression_check --test-id comparison_rag_hallucination --verbose
```

**Khi nào dùng:**
- Debug một test case cụ thể
- Xem chi tiết memo output và evidence
- Calibrate thresholds

**Output:**
```
🔍 Running: comparison_rag_hallucination

Query: "How can retrieval-augmented generation..."

📊 Retrieved Evidence:
  - 13 papers (5 code, 8 non-code)
  - Code ratio: 38% (✓ within 40% limit)

📝 Generated Memo:
  - Word count: 5234 (target: 5200-5800)
  - Must coverage: 78% (✓ >= 65%)
  - Quantitative findings: Present (3 rows)
  - Worked example: Single-source [12] (✓)
  
✅ PASS: All checks passed
```

---

## 3️⃣ Pre-Commit Hook (Tự Động)

### Enable pre-commit hook:
```bash
cd /workspace
chmod +x scripts/pre-commit.sh
ln -s ../../scripts/pre-commit.sh .git/hooks/pre-commit
```

**Cách hoạt động:**
- Tự động chạy **fast check** (3 tests) trước mỗi commit
- Nếu FAIL → commit bị block
- Nếu PASS → commit được phép

**Bypass (khi cần thiết):**
```bash
git commit --no-verify -m "WIP: debugging"
```

---

## 4️⃣ CI Integration (GitHub Actions)

**Workflow đã được config tại:** `.github/workflows/regression.yml`

**Trigger:**
- Mỗi push lên `main` / `develop`
- Mỗi PR mới hoặc update PR

**Xem kết quả:**
1. Vào PR trên GitHub
2. Check tab "Checks" → "Regression Tests"
3. Nếu FAIL, xem logs để debug

**Ví dụ logs:**
```
Run regression tests
  ✓ Setup Python 3.11
  ✓ Install dependencies
  ✓ Start test DB
  ⊗ Run regression_check
    FAILED: 2/15 tests failed
    
    Details:
    - benchmark_quantitative: Missing quantitative section
    - sparse_evidence: Hallucination rate 18% (> 15%)
```

---

## 5️⃣ Troubleshooting

### ❌ Test fails: "ModuleNotFoundError: No module named 'app'"
**Fix:**
```bash
# Run from correct directory
cd /workspace/apps/agent
export PYTHONPATH=/workspace/apps/agent:$PYTHONPATH
python -m app.eval.regression_check
```

### ❌ Test fails: "Database connection error"
**Fix:**
```bash
# Start DB container
docker compose up -d db

# Or use mock mode (no DB)
python -m app.eval.regression_check --mock
```

### ❌ Test fails: "Rate limit exceeded (429)"
**Fix:**
```bash
# Add delay between tests
python -m app.eval.regression_check --delay 5  # 5 seconds between tests

# Or use cached evidence (no API calls)
python -m app.eval.regression_check --use-cache
```

### ❌ Test passes locally but fails on CI
**Possible causes:**
- LLM non-deterministic output → Enable deterministic mode in CI
- Different environment variables → Check `.env` vs GitHub Secrets
- Cached data locally → Clear cache and re-run

**Fix:**
```bash
# Clear cache
rm -rf /tmp/kiln_test_cache/*

# Re-run with deterministic LLM params
export LLM_TEMPERATURE=0
export LLM_SEED=42
python -m app.eval.regression_check
```

---

## 6️⃣ Adding New Test Cases

### Bước 1: Thêm vào `golden_set.json`
```json
{
  "id": "my_new_test",
  "query": "Your test query here",
  "type": "comparison",
  "expected": {
    "has_quantitative": true,
    "min_must_coverage": 70,
    "max_code_ratio": 0.40,
    "min_word_count": 4000
  }
}
```

### Bước 2: Run test để lấy baseline
```bash
python -m app.eval.regression_check --test-id my_new_test --save-baseline
```

### Bước 3: Verify baseline
```bash
# Check output in apps/agent/data/eval/baselines/my_new_test.json
cat apps/agent/data/eval/baselines/my_new_test.json
```

### Bước 4: Commit
```bash
git add apps/agent/data/eval/golden_set.json
git add apps/agent/data/eval/baselines/my_new_test.json
git commit -m "test: add regression case for my_new_test"
```

---

## 7️⃣ Interpreting Results

### ✅ PASS - Test passed
```
✓ comparison_rag_hallucination
  Must coverage: 78% (≥65% ✓)
  Code ratio: 38% (≤40% ✓)
  Quantitative: Present ✓
```
→ **No action needed**

### ✗ FAIL - Logic regression detected
```
✗ comparison_rag_hallucination
  Must coverage: 58% (≥65% ✗)
  Retrieval returned only 4 papers
```
→ **Fix the bug, then re-run**

### ⊘ SKIP - Test skipped (expected failure)
```
⊘ out_of_scope_pokemon
  Reason: Out-of-scope query (by design)
```
→ **No action needed (expected behavior)**

### ⚠️ WARN - Passed but close to threshold
```
⚠ benchmark_quantitative
  Must coverage: 66% (≥65% ✓, but close)
  Consider: More retrieval depth?
```
→ **Monitor this test case (may fail in future)**

---

## 8️⃣ Performance Benchmarks

**Fast check (3 tests):**
- Time: ~2-3 minutes
- API calls: ~30 (10 per test)
- Cost: ~$0.15

**Full suite (15 tests):**
- Time: ~10-15 minutes
- API calls: ~150 (10 per test)
- Cost: ~$0.75

**Tips để giảm cost:**
```bash
# Use cached evidence (skip retrieval)
python -m app.eval.regression_check --use-cache

# Use cheaper LLM for non-critical tests
export WRITER_MODEL=gpt-4o-mini
python -m app.eval.regression_check
```

---

## 9️⃣ Q&A

**Q: Có cần chạy regression test mỗi lần sửa code nhỏ không?**  
A: Không cần thiết. Chỉ chạy khi:
- Sửa logic core (retrieval, writer, coverage)
- Thêm feature mới
- Trước khi merge PR

**Q: Test FAIL nhưng tôi chắc code đúng, làm sao?**  
A: Có thể threshold cần calibrate:
1. Chạy `--verbose` để xem chi tiết
2. Nếu output hợp lý nhưng threshold quá strict → Update `expected` trong `golden_set.json`
3. Commit new baseline

**Q: CI chạy regression test mỗi PR, có tốn budget không?**  
A: Có, mỗi PR run ~$0.75. Nếu muốn tiết kiệm:
- Chỉ trigger CI cho branch `main`/`develop`
- Hoặc dùng `--fast` trên CI (3 tests thay vì 15)

**Q: Regression test có catch được semantic hallucination không?**  
A: Chỉ catch được **structural** và **coverage** regressions. Để catch semantic issues, cần:
- Manual review (Tier-B audit)
- Semantic validation (#4 - đã implement)

---

## 🎯 Summary

| Scenario | Command | Time | When to Use |
|----------|---------|------|-------------|
| Quick validation | `python -m app.eval.regression_check --fast` | 2-3 min | Before commit |
| Full validation | `python -m app.eval.regression_check` | 10-15 min | Before PR merge |
| Debug single test | `python -m app.eval.regression_check --test-id <id> --verbose` | 1 min | Debugging |
| Pre-commit hook | (automatic) | 2-3 min | Every commit |
| CI workflow | (automatic on push) | 10-15 min | Every PR |

**Best practice workflow:**
1. Develop feature → Run fast check locally
2. Feature complete → Run full suite locally
3. Push to PR → CI runs full suite
4. PR approved → Merge (CI re-runs on `main`)

---

**Cần hỗ trợ thêm? Ping @quangg1 hoặc xem logs tại:**
- Local: `apps/agent/data/eval/logs/`
- CI: GitHub Actions → Checks tab
