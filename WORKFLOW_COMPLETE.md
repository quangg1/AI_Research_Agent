# Kiln Research Agent - Complete Workflow

## 🔄 End-to-End Flow: UI → Research → Output → Benchmark

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PHASE 1: USER INPUT (UI)                        │
└─────────────────────────────────────────────────────────────────────────┘

User types query in Web UI (React)
         ↓
    "How to implement RAG with <2s latency?"
         ↓
    Click "Start Research"
         ↓
┌────────────────────────────────────────────────────────────────┐
│  Web (React + Vite)                                            │
│  - packages/web/src/pages/research/                            │
│  - Submit button → POST /api/research/runs                     │
└────────────────────────────────────────────────────────────────┘
         ↓
         
┌─────────────────────────────────────────────────────────────────────────┐
│                        PHASE 2: API LAYER (NestJS)                      │
└─────────────────────────────────────────────────────────────────────────┘

NestJS API receives request
         ↓
    apps/api/src/research/research.controller.ts
         ↓
    Validate: user auth, org quota, API keys
         ↓
    Create research_run record in Postgres
         ↓
    Push job to BullMQ (Redis queue)
         ↓
┌────────────────────────────────────────────────────────────────┐
│  API Layer Actions:                                            │
│  1. Auth check (Clerk JWT)                                     │
│  2. Create run_id in database                                  │
│  3. Enqueue job: {query, org_id, run_id, user_id}            │
│  4. Return SSE stream URL to web                               │
└────────────────────────────────────────────────────────────────┘
         ↓
         
┌─────────────────────────────────────────────────────────────────────────┐
│                    PHASE 3: AGENT PIPELINE (Python)                     │
└─────────────────────────────────────────────────────────────────────────┘

BullMQ worker picks up job
         ↓
    POST /internal/v1/executions/stream
         ↓
    apps/agent/app/main.py
         ↓
    runtime.stream_execution()
         ↓
┌────────────────────────────────────────────────────────────────┐
│  LangGraph Pipeline (apps/agent/app/graph/builder.py)         │
│                                                                 │
│  1. BRIEFING NODE                                              │
│     - Parse query intent                                       │
│     - Generate must-answer dimensions                          │
│     - Check out_of_scope                                       │
│     OUTPUT: ResearchBrief                                      │
│                                                                 │
│  2. PLANNER NODE                                               │
│     - Classify query_type (FACTUAL/COMPARATIVE/...)           │
│     - Configure budget (28 retrieval + 24 enrich)             │
│     - Generate falsification sub-queries                       │
│     - Check knowledge cache (reuse?)                           │
│     OUTPUT: Plan                                               │
│     ↓                                                           │
│  3. PLAN_GATE (HITL)                                           │
│     - INTERRUPT: User reviews plan                             │
│     - User can edit sub-queries                                │
│     - User approves → continue                                 │
│     ↓                                                           │
│  4. PARALLEL RETRIEVAL (search ∥ scholar ∥ docs)              │
│     ┌─────────────┬──────────────┬─────────────┐             │
│     │   SEARCH    │   SCHOLAR    │    DOCS     │             │
│     │  (Tavily)   │ (OpenAlex+S2)│  (Qdrant)   │             │
│     │  Web docs   │ Academic     │ Internal    │             │
│     │  Vendor     │ papers       │ corpus      │             │
│     └─────────────┴──────────────┴─────────────┘             │
│            ↓              ↓              ↓                     │
│     Merge at COLLECTOR                                        │
│     - Deduplicate URLs                                        │
│     - Rank by authority + numeric bias                        │
│     OUTPUT: ~20-30 evidence items                             │
│     ↓                                                           │
│  5. ENRICH NODE                                                │
│     - Full-page fetch for top URLs                            │
│     - Charge enrich pool (24 cap)                             │
│     - Gap-aware: fetch more for weak slots                    │
│     OUTPUT: Evidence with full_text                           │
│     ↓                                                           │
│  6. RETRIEVE NODE ⭐ PER-DIMENSION                             │
│     - For each must-answer dimension:                          │
│       * Hybrid retrieval (BM25 + embeddings)                  │
│       * k=3-5 passages per dimension                           │
│       * Targeted query per slot                                │
│     - Embeddings: text-embedding-004                          │
│     OUTPUT: Dimension-specific evidence chunks                │
│     ↓                                                           │
│  7. EXTRACT NODE                                               │
│     - Quote extraction per dimension                           │
│     - Claim seed generation                                    │
│     - Paper concept extraction (if has_papers)                │
│     - Dimension refinement (evidence-first)                   │
│     OUTPUT: Quotes + claims per dimension                     │
│     ↓                                                           │
│  8. CRITIC NODE                                                │
│     - Coverage check: all must-answer filled?                 │
│     - Contradiction detection                                  │
│     - Generate followup sub-queries for gaps                  │
│     - Calculate confidence with penalties:                     │
│       * -3 pts per open gap                                    │
│       * -2 pts per weak evidence                               │
│       * -5 pts if sparse sources                               │
│     OUTPUT: CriticVerdict                                      │
│     ↓                                                           │
│     Decision:                                                   │
│     - sufficient + budget OK → HITL                            │
│     - insufficient + budget OK → PLANNER (loop)               │
│     - insufficient + budget exhausted → HITL (warning)        │
│     ↓                                                           │
│  9. HITL (HITL)                                                │
│     - INTERRUPT: User reviews evidence coverage               │
│     - User can:                                                │
│       * Approve → continue to report                           │
│       * Revise → back to planner                               │
│     ↓                                                           │
│  10. REPORT NODE                                               │
│      - Section-wise deep generation (race_write.py)           │
│      - Phases:                                                 │
│        1. Analysis (dimensions + quantitative table)          │
│        2. Back matter (uncertainties + decision rules)        │
│      - Post-processing:                                        │
│        * Overclaim softening ⭐                                │
│        * Template leakage cleanup ⭐                           │
│        * Citation deduplication                                │
│        * Confidence calibration ⭐                             │
│      - Quality checks:                                         │
│        * Source saturation                                     │
│        * Citation stacking                                     │
│        * Template leaks                                        │
│      - If quality fail → _regenerate_for_quality (max 2×)    │
│      OUTPUT: Report (5500 words)                              │
│      ↓                                                          │
│  11. MEMO_GATE (HITL)                                          │
│      - INTERRUPT: User reviews final memo                      │
│      - Quality validation:                                     │
│        * All quality checks pass?                              │
│        * Confidence score calibrated?                          │
│        * No overclaims detected?                               │
│      - User can:                                               │
│        * Approve → PUBLISH                                     │
│        * Revise → back to critic or report                     │
│      ↓                                                          │
│  12. PUBLISH ✅                                                │
│      - Save to knowledge base                                  │
│      - Update research_runs.result_json                        │
│      - Generate share link                                     │
└────────────────────────────────────────────────────────────────┘
         ↓

┌─────────────────────────────────────────────────────────────────────────┐
│                     PHASE 4: OUTPUT TO USER (Web UI)                    │
└─────────────────────────────────────────────────────────────────────────┘

SSE stream sends progress to Web
         ↓
    UI renders:
    - Progress bar per node
    - Evidence cards (collector)
    - Coverage status (critic)
    - Final memo markdown
         ↓
┌────────────────────────────────────────────────────────────────┐
│  User sees:                                                     │
│  ✅ Executive Summary                                          │
│  ✅ Quantitative Findings Table                                │
│  ✅ Analysis (per dimension)                                   │
│  ✅ Uncertainties & Gaps                                       │
│  ✅ Decision Rules                                             │
│  ✅ Citations ([1 peer], [2 specialist], [3 web])            │
│  ✅ Confidence: 75/100 (calibrated)                           │
└────────────────────────────────────────────────────────────────┘
         ↓
    User actions:
    - Pin memo
    - Share link
    - Follow-up research
    - Export PDF
         ↓

┌─────────────────────────────────────────────────────────────────────────┐
│                    PHASE 5: EVALUATION & BENCHMARK                      │
└─────────────────────────────────────────────────────────────────────────┘

After memo is published, run quality audit:

┌────────────────────────────────────────────────────────────────┐
│  TIER-A: Deterministic Gates (Runtime)                         │
│  File: apps/agent/app/eval/trust_bench.py                      │
│                                                                 │
│  Checks during pipeline:                                       │
│  ✓ Citation format validation                                  │
│  ✓ Folklore blocking                                           │
│  ✓ Numeric extraction semantic gates                           │
│  ✓ Template placeholder detection                              │
│                                                                 │
│  → Blocks publication if fails                                 │
└────────────────────────────────────────────────────────────────┘
         ↓

┌────────────────────────────────────────────────────────────────┐
│  TIER-B: Independent Audit (Post-Publish)                      │
│  File: apps/agent/app/eval/trust_bench_e2e.py                  │
│                                                                 │
│  Step 1: EXPORT                                                │
│  $ python -m app.eval.trust_bench_e2e export <run_id>         │
│  → Creates snapshot JSON with memo + sources                   │
│                                                                 │
│  Step 2: BUILD                                                 │
│  $ python -m app.eval.trust_bench_e2e build <snapshot>        │
│  → Generates audit_packet.md:                                  │
│    - Extracted claims (numbered)                               │
│    - Source excerpts for each claim                            │
│                                                                 │
│  Step 3: JUDGE (Manual or LLM)                                 │
│  - Paste audit_packet.md to Gemini/ChatGPT/Grok              │
│  - LLM evaluates each claim:                                   │
│    * SUPPORTED: Claim matches source                           │
│    * NOT_SUPPORTED: Hallucination detected                     │
│    * CANNOT_VERIFY: Source missing/unclear                     │
│  - Save verdicts to verdicts.json                              │
│                                                                 │
│  Step 4: SCORE                                                 │
│  $ python -m app.eval.trust_bench_e2e score <snapshot>        │
│  → Calculates hallucination rate:                              │
│    hallucination_rate = NOT_SUPPORTED / (SUPPORTED + NOT_SUPPORTED)
│  → Saves to data/eval/trust_bench_e2e_history.jsonl          │
└────────────────────────────────────────────────────────────────┘
         ↓

┌────────────────────────────────────────────────────────────────┐
│  OTHER BENCHMARKS                                              │
│                                                                 │
│  1. Domain Eval (apps/agent/app/eval/runner.py)              │
│     $ python -m app.eval.runner                               │
│     - Tests query classification                               │
│     - Folklore blocking                                        │
│     - Golden set validation                                    │
│                                                                 │
│  2. Graph Routing (apps/agent/app/eval/graph_routing.py)     │
│     $ python -m app.eval.graph_routing                        │
│     - Coverage gate transitions                                │
│     - Budget exhaustion handling                               │
│                                                                 │
│  3. RACE Metrics (apps/agent/app/eval/race_bench.py)         │
│     $ python -m app.eval.race_bench                           │
│     - Memo structure validation                                │
│     - Grounding proxy metrics                                  │
│                                                                 │
│  4. Unit Tests (pytest)                                        │
│     $ cd apps/agent && pytest                                  │
│     - 180+ tests across domain/graph/report                    │
└────────────────────────────────────────────────────────────────┘
         ↓

┌─────────────────────────────────────────────────────────────────────────┐
│                         PHASE 6: METRICS & TRACKING                     │
└─────────────────────────────────────────────────────────────────────────┘

All results saved to:

┌────────────────────────────────────────────────────────────────┐
│  Database (PostgreSQL)                                         │
│  - research_runs table:                                        │
│    * status, progress, result_json                             │
│    * retrieval_count, enrich_count                             │
│    * confidence_score, source_count                            │
│  - events table:                                               │
│    * Node completions                                          │
│    * HITL interrupts                                           │
│    * Quality regenerations                                     │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Files (data/eval/)                                            │
│  - trust_bench_e2e_history.jsonl                              │
│    * Hallucination rate per run                                │
│    * Flagged claims with reasons                               │
│  - golden_set.json                                             │
│    * Reference queries for regression testing                  │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Metrics Dashboard (Internal)                                  │
│  - Average confidence: 75/100                                  │
│  - Hallucination rate: <10%                                    │
│  - Source diversity: 12 unique domains avg                     │
│  - Quality regenerations: 15% of runs                          │
│  - HITL approval rate: 92%                                     │
└────────────────────────────────────────────────────────────────┘
```

---

## 📊 Timeline cho một research run điển hình

```
Time    | Stage           | Activity
--------|-----------------|------------------------------------------
T+0s    | UI              | User submits query
T+1s    | API             | Auth, create run, enqueue job
T+2s    | Briefing        | Parse intent, generate dimensions
T+5s    | Planner         | Classify, budget, sub-queries
T+7s    | Plan Gate       | HITL - user reviews (30s avg)
T+37s   | Search          | Tavily retrieves 10 URLs
T+37s   | Scholar         | OpenAlex retrieves 8 papers
T+37s   | Docs            | Qdrant retrieves 5 internal docs
T+40s   | Collector       | Merge, dedupe, rank → 23 evidence items
T+45s   | Enrich          | Fetch full text for top 15 URLs
T+60s   | Retrieve        | Per-dimension hybrid retrieval (k=5×6 dims)
T+65s   | Extract         | Quote extraction + dimension refinement
T+70s   | Critic          | Coverage check → sufficient
T+72s   | HITL            | User reviews evidence (15s avg)
T+87s   | Report          | Generate 5500-word memo
T+120s  | Memo Gate       | HITL - user reviews memo (10s avg)
T+130s  | Publish         | Save to knowledge base
--------|-----------------|------------------------------------------
TOTAL   | ~2-3 minutes    | End-to-end (with HITL pauses)
```

---

## 🔄 Feedback Loops trong Pipeline

### Loop 1: Critic → Planner (Research Gap Loop)
```
Critic detects insufficient coverage
         ↓
Generate followup sub-queries
         ↓
Back to Planner with new questions
         ↓
Retrieve more evidence
         ↓
Extract + Critic again
         ↓
(Max 6 iterations or budget exhausted)
```

### Loop 2: Memo_gate → Report (Quality Regeneration Loop)
```
Memo_gate detects quality issues:
- Source saturation (too few unique domains)
- Citation stacking (same paper cited 10× in one section)
- Template leakage ("THEN WE ADDRESS...")
         ↓
Trigger _regenerate_for_quality()
         ↓
Rewrite memo from existing notes (no new retrieval)
         ↓
Memo_gate validates again
         ↓
(Max 2 regenerations)
```

### Loop 3: Report → Planner (Integrity Re-loop)
```
Report detects integrity gaps:
- Contradictions between quant table & decision rules
- Claim without supporting citation
         ↓
Set status=integrity_research
         ↓
Back to Planner with targeted queries
         ↓
Retrieve additional evidence
         ↓
Report again with new evidence
```

---

## 🎯 Quality Gates Summary

| Gate | Location | Blocking? | Trigger |
|------|----------|-----------|---------|
| **Out of scope** | Briefing | ✅ Yes | Non-LLM-systems query |
| **Folklore check** | Throughout | ✅ Yes | Forbidden unverified claims |
| **Coverage gate** | Critic | ⚠️ Warning | Insufficient must-answer slots |
| **Budget exhausted** | Critic | ⚠️ Warning | Retrieval/enrich caps hit |
| **Quality checks** | Memo_gate | 🔄 Regenerate | Saturation, stacking, leaks |
| **HITL approval** | Plan/HITL/Memo gates | 👤 Human | User must approve |

---

## 📈 Success Metrics

| Metric | Target | Current (2026 Q3) |
|--------|--------|-------------------|
| **End-to-end latency** | <5 min | ~2-3 min (with HITL) |
| **Hallucination rate** | <15% | <10% (Trust Bench) |
| **Source diversity** | >8 domains | ~12 domains avg |
| **Confidence calibration** | <95% when gaps | 55-95 range ✅ |
| **Quality regenerations** | <20% of runs | ~15% ✅ |
| **HITL approval rate** | >85% | 92% ✅ |
| **Per-dimension retrieval** | 100% coverage | 100% ✅ |

---

## 🚀 Quick Commands

### Run full pipeline (local)
```bash
# Start services
docker-compose up -d

# Submit query via CLI
SHOWCASE_MODE=true python -m app.eval.showcase_run "Your question?"
```

### Run benchmarks
```bash
cd apps/agent

# Domain + routing eval
python -m app.eval.runner

# Trust Bench E2E
python -m app.eval.trust_bench_e2e export <run_id>
python -m app.eval.trust_bench_e2e build <snapshot>
python -m app.eval.trust_bench_e2e score <snapshot>

# Unit tests
pytest
```

---

**TÓM TẮT FLOW:**
1. **UI → API:** User query → NestJS → BullMQ
2. **Agent Pipeline:** 12 nodes (briefing → planner → retrieve → extract → critic → report → publish)
3. **Output:** 5500-word memo với citations, confidence score, uncertainties
4. **Benchmark:** Trust Bench E2E (independent LLM judge) + domain evals + unit tests
5. **Metrics:** Hallucination <10%, confidence calibrated, source diversity >12 domains

**Thời gian:** ~2-3 phút (có HITL), ~1-2 phút (auto-approve showcase mode)
