# Trust Bench E2E - Quick Reference

## 📋 Workflow Overview

```
1. export  → Run JSON dump → Compact snapshot (.json)
2. build   → Snapshot → Audit packet (.md) + Claims (.claims.json)  
3. score   → Claims + Verdicts → Hallucination rate + History (.jsonl)
```

---

## 🚀 Commands

### Step 1: Export (Already Done)
```powershell
python -m app.eval.trust_bench_e2e export <run_dump.json> --out data/eval/trust_bench_e2e_memos/<name>.json
```

**Example:**
```powershell
python -m app.eval.trust_bench_e2e export research_run_6d4b.json --out data/eval/trust_bench_e2e_memos/multi_agent_run_6d4b.json
```

---

### Step 2: Build Audit Packet
```powershell
python -m app.eval.trust_bench_e2e build data/eval/trust_bench_e2e_memos/<name>.json --out audit_packet.md --n-claims 10
```

**Output:**
- `audit_packet.md` - Markdown để paste vào ChatGPT/Grok
- `audit_packet.claims.json` - Claims metadata (cần cho step 3)

---

### Step 3: Get Verdicts from LLM

**Manual step:**
1. Copy nội dung `audit_packet.md`
2. Paste vào ChatGPT, Grok, hoặc Gemini
3. Prompt: "Please evaluate these claims and return JSON verdicts"
4. Save response as `verdicts.json`

**Expected format:**
```json
[
  {
    "id": 1,
    "verdict": "SUPPORTED",
    "reason": "The excerpt explicitly states..."
  },
  ...
]
```

---

### Step 4: Score (Calculate Hallucination Rate)
```powershell
python -m app.eval.trust_bench_e2e score audit_packet.claims.json verdicts.json
```

**Output:**
```json
{
  "n_claims": 10,
  "supported": 6,
  "not_supported": 3,
  "cannot_verify": 1,
  "hallucination_rate": 0.333,
  "flagged_claims": [...]
}
```

**Also saves to:** `data/eval/trust_bench_e2e_history.jsonl`

---

## ⚡ Quick Run (With Automation Script)

```powershell
# Make sure you're in project root and conda base is activated
.\run_trust_bench_score.ps1
```

This script:
1. ✅ Checks files exist
2. ✅ Creates verdicts.json if needed
3. ✅ Runs score command
4. ✅ Shows results
5. ✅ Saves to history

---

## 📊 Results Interpretation

### Hallucination Rate
```
hallucination_rate = NOT_SUPPORTED / (SUPPORTED + NOT_SUPPORTED)
```

**Example:**
- 10 total claims
- 6 SUPPORTED
- 3 NOT_SUPPORTED  
- 1 CANNOT_VERIFY

**Rate:** 3 / (6 + 3) = 0.333 = **33.3% hallucination rate**

### Verdicts
- **SUPPORTED** ✅ - Excerpt contains this exact number/fact
- **NOT_SUPPORTED** ❌ - Excerpt doesn't support claim (hallucination!)
- **CANNOT_VERIFY** ⚠️ - Excerpt too short/unclear (excluded from rate)

---

## 📁 File Structure

```
D:\AI_Research_Agent\
├── data\eval\
│   ├── trust_bench_e2e_memos\
│   │   └── multi_agent_run_6d4b.json  ← Exported snapshot
│   ├── verdicts_gemini.json            ← LLM verdicts
│   └── trust_bench_e2e_history.jsonl   ← Results history
├── apps\agent\app\eval\
│   ├── trust_bench_e2e.py              ← Main script
│   └── trust_bench.py                  ← Tier-A checks
└── run_trust_bench_score.ps1           ← Automation helper
```

---

## 🔍 Checking History

```powershell
# View all historical results
Get-Content data\eval\trust_bench_e2e_history.jsonl | ConvertFrom-Json | Format-Table

# Get latest result
Get-Content data\eval\trust_bench_e2e_history.jsonl | Select-Object -Last 1 | ConvertFrom-Json
```

---

## 💡 Tips

1. **N Claims:** Default is 8, use `--n-claims 10` for more thoroughness
2. **Multiple Runs:** Each score saves a new line to history.jsonl
3. **Git SHA:** Automatically tracked in history for reproducibility
4. **Judge Model:** Use different LLMs (ChatGPT, Grok, Gemini) to compare verdicts

---

## 🎯 Current Status (Your Run)

```
✅ Exported: multi_agent_run_6d4b.json
✅ Claims: audit_packet.claims.json (10 claims)
✅ Verdicts: From Gemini (10 verdicts)
🔄 Ready to score!
```

**Results Summary (from Gemini verdicts):**
- SUPPORTED: 6
- NOT_SUPPORTED: 3 (claims 1, 7, 10)
- CANNOT_VERIFY: 1 (claim 3)
- **Expected Hallucination Rate: 33.3%**

---

## 🚀 Run Now:

```powershell
.\run_trust_bench_score.ps1
```
