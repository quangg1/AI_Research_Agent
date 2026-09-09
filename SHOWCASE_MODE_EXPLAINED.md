# SHOWCASE_MODE vs UI_MODE - HITL Gates Comparison

## 🔄 TẠI SAO CÓ 2 MODES?

### **UI MODE (enable_hitl=True)** - Production, có user interaction
```python
# apps/agent/app/graph/builder.py line 112
def build_graph(checkpointer=None, enable_hitl: bool = True):
    builder.add_node("plan_gate", plan_gate_node)      # ← Node có interrupt()
    builder.add_node("hitl", hitl_node)                 # ← Node có interrupt()
    builder.add_node("memo_gate", memo_gate_node)       # ← Node có interrupt()
```

### **SHOWCASE MODE (enable_hitl=False)** - Testing, auto-pass gates
```python
# apps/agent/app/eval/showcase_run.py line 109
graph = build_test_graph(enable_hitl=False)  # ← TẮT HITL!

# builder.py sẽ dùng:
builder.add_node("plan_gate", plan_gate_node_auto)   # ← Auto-approve
builder.add_node("hitl", hitl_node_auto)             # ← Auto-approve
builder.add_node("memo_gate", memo_gate_node_auto)   # ← Auto-approve
```

---

## 🛑 SO SÁNH 3 GATES:

### **GATE 1: PLAN_GATE**

#### UI Mode (có interrupt):
```python
# apps/agent/app/graph/nodes/plan_gate.py line 12-38
def plan_gate_node(state: ResearchState) -> dict:
    # ... prepare payload ...
    
    event("plan_gate_interrupt", agents=state.get("agents_to_run"))
    decision = interrupt(payload)  # ⏸️ ĐỢI USER CLICK!
    
    action = decision.get("action", "start")
    if action == "cancel":
        return {"status": "cancelled"}
    
    # User có thể edit sub_queries, agents_to_run
    patch = decision.get("plan") or {}
    merged_plan = dict(plan)
    # ... merge user edits ...
    
    return {"plan_confirmed": True, "human_decision": decision}
```

**User có thể:**
- ✏️ Edit sub-queries
- ✏️ Edit scholar queries
- ✏️ Add/remove agents (search, scholar, docs)
- ✏️ Add assumptions
- ❌ Cancel research

**UI Display:**
```
┌─────────────────────────────────────────┐
│  Review Agent Plan                      │
├─────────────────────────────────────────┤
│  Sub-queries:                           │
│  1. "What is RAG latency bottleneck?"  │
│  2. "Vector DB query performance?"      │
│                                         │
│  Agents: [search] [scholar] [docs]     │
│                                         │
│  [Cancel]  [Edit]  [Approve] ←         │
└─────────────────────────────────────────┘
         ⏸️ Agent PAUSE ở đây!
```

#### Showcase Mode (auto):
```python
# plan_gate.py line 80-81
def plan_gate_node_auto(state: ResearchState) -> dict:
    return {"plan_confirmed": True}  # ← Approve ngay, KHÔNG đợi!
```

**Flow:**
```
Planner → Plan_gate_auto → Search/Scholar/Docs
            (0.001s)           (tiếp tục ngay)
```

---

### **GATE 2: HITL (Evidence Review)**

#### UI Mode:
```python
# apps/agent/app/graph/nodes/hitl.py
def hitl_node(state: ResearchState) -> dict:
    gate = gate_from_critic(critic)
    
    if gate.get("gate_reason") == "sufficient":
        # Evidence đủ rồi, có thể skip HITL
        if settings.auto_approve_sufficient:
            return {"status": "approved"}
    
    # Prepare evidence summary
    payload = {
        "type": "evidence_review",
        "coverage": critic.get("coverage"),
        "contradictions": critic.get("contradictions"),
        "quality_score": research_quality(state),
        "evidence_count": len(evidence),
    }
    
    decision = interrupt(payload)  # ⏸️ ĐỢI USER!
    
    if decision.get("action") == "revise":
        return {"status": "revising"}  # → Back to planner
    
    return {"status": "approved"}  # → Continue to report
```

**User thấy:**
```
┌─────────────────────────────────────────┐
│  Evidence Review                        │
├─────────────────────────────────────────┤
│  Coverage: 5/6 dimensions filled       │
│  Quality: 78/100                       │
│  Sources: 23 evidence items            │
│                                         │
│  Open gaps:                             │
│  - Latency benchmarks (weak)           │
│                                         │
│  [Request More Research]  [Approve] ←   │
└─────────────────────────────────────────┘
         ⏸️ Agent PAUSE ở đây!
```

#### Showcase Mode:
```python
def hitl_node_auto(state: ResearchState) -> dict:
    return {"status": "approved"}  # ← Auto-approve!
```

---

### **GATE 3: MEMO_GATE (Final Review)**

#### UI Mode:
```python
# apps/agent/app/graph/nodes/memo_gate.py
def memo_gate_node(state: ResearchState) -> dict:
    report = state.get("report") or {}
    
    # Quality checks
    quality_issues = detect_quality_issues(report)
    if quality_issues and regenerations < MAX_QUALITY_REGENERATIONS:
        # Tự động regenerate, KHÔNG cần user confirm
        return {"status": "quality_regenerate"}
    
    # Show final memo to user
    payload = {
        "type": "memo_review",
        "report": report,
        "confidence": research_quality(state),
        "quality_issues": quality_issues,
    }
    
    decision = interrupt(payload)  # ⏸️ ĐỢI USER!
    
    if decision.get("action") == "revise":
        return {"status": "revising"}  # → Back to critic/report
    
    return {"status": "approved"}  # → Publish!
```

**User thấy:**
```
┌─────────────────────────────────────────┐
│  Final Memo Review                      │
├─────────────────────────────────────────┤
│  Word count: 5,482                     │
│  Citations: 18 sources                 │
│  Confidence: 75/100                    │
│                                         │
│  [View Memo] ←                         │
│                                         │
│  Quality: ✅ All checks passed         │
│                                         │
│  [Revise]  [Approve & Publish] ←       │
└─────────────────────────────────────────┘
         ⏸️ Agent PAUSE ở đây!
```

#### Showcase Mode:
```python
def memo_gate_node_auto(state: ResearchState) -> dict:
    return {"status": "approved"}  # ← Auto-publish!
```

---

## 📊 SO SÁNH THỜI GIAN:

### UI Mode (Production):
```
Timeline:
T+0s    User submit
T+5s    Planner done
T+7s    🛑 PLAN_GATE → User review (30s avg)
T+37s   Retrieval done
T+70s   🛑 HITL → User review evidence (15s avg)
T+85s   Report done
T+120s  🛑 MEMO_GATE → User review memo (10s avg)
T+130s  Published

Total: ~2-3 minutes (includes ~55s of human pauses)
Active agent time: ~1-2 minutes
Human review time: ~55 seconds
```

### Showcase Mode:
```
Timeline:
T+0s    Start
T+5s    Planner done
T+5s    ✅ Auto-pass plan_gate (0.001s)
T+37s   Retrieval done
T+70s   ✅ Auto-pass HITL (0.001s)
T+85s   Report done
T+120s  ✅ Auto-pass memo_gate (0.001s)
T+120s  Published

Total: ~1-2 minutes (NO human pauses)
All time: Pure agent processing
```

---

## 🎯 KHI NÀO DÙNG SHOWCASE_MODE?

### ✅ Use Cases:
1. **Demo/Testing**: Cần chạy nhanh để test pipeline
2. **Benchmarking**: Đo performance không có human latency
3. **Batch processing**: Chạy nhiều queries tự động
4. **CI/CD**: Automated testing trong pipeline
5. **Development**: Debug code mà không cần click UI

### ❌ KHÔNG dùng Showcase Mode khi:
1. **Production research**: User cần control flow
2. **Billing concerns**: User muốn review plan trước khi spend credits
3. **Quality control**: User muốn review evidence quality
4. **Custom queries**: User muốn edit sub-queries
5. **Compliance**: Cần human approval cho research output

---

## 🛠️ CODE FLOW COMPARISON:

### UI Mode (Web → API → Agent):
```python
# apps/web/src/pages/research → User clicks
#     ↓
# apps/api/src/research/research.controller.ts
#     ↓ POST /api/research/runs
# research.service.ts → enqueue BullMQ job
#     ↓
# agent-execution.client.ts → POST /internal/v1/executions/stream
#     ↓
# apps/agent/app/main.py → stream_execution()
#     ↓
# runtime.py → build_graph(enable_hitl=True)  ← HITL ON!
#     ↓
# graph runs with interrupt() calls
#     ↓ SSE stream back to web
# User sees gates, clicks approve
```

### Showcase Mode (CLI):
```python
# Terminal: SHOWCASE_MODE=true python -m app.eval.showcase_run "query"
#     ↓
# apps/agent/app/eval/showcase_run.py
#     ↓ line 20
# os.environ["SHOWCASE_MODE"] = "true"
#     ↓ line 109
# graph = build_test_graph(enable_hitl=False)  ← HITL OFF!
#     ↓
# graph runs with auto-approve nodes
#     ↓
# No interrupts, runs end-to-end
#     ↓
# Output saved to data/showcase/last_run.json
```

---

## 📋 ENVIRONMENT VARIABLES:

### UI Mode:
```bash
# .env
SHOWCASE_MODE=false                # ← Default, HITL enabled
MAX_RETRIEVAL_CALLS=28            # Normal budget
MAX_ENRICH_CALLS=24
MAX_ITERATIONS=6
```

### Showcase Mode:
```bash
# .env or command line
SHOWCASE_MODE=true                 # ← Auto-approve all gates
SHOWCASE_RETRIEVAL_POOL=48        # ← Higher budget for demos!
SHOWCASE_ENRICH_POOL=36
SHOWCASE_MAX_ITERATIONS=10
```

**Tại sao budget cao hơn?**
- Demo cần comprehensive results
- Không có user edit nên cần retrieve đủ ngay lần đầu
- Muốn showcase "best possible" quality

---

## 🔍 DEBUG: XEM MODE ĐANG CHẠY

### Check trong code:
```python
from app.config import settings

if settings.showcase_mode:
    print("Running in SHOWCASE mode - auto-approve all gates")
else:
    print("Running in UI mode - HITL gates enabled")
```

### Check trong logs:
```bash
# UI mode
[2026-09-06 06:30:15] plan_gate_interrupt agents=['search', 'scholar']
[2026-09-06 06:30:45] plan_gate_confirmed action=start

# Showcase mode
[2026-09-06 06:30:15] plan_gate skipped=auto
```

---

## ✅ TÓM TẮT:

| Feature | UI Mode | Showcase Mode |
|---------|---------|---------------|
| **HITL Gates** | 3 gates (plan, evidence, memo) | 0 gates - auto-pass all |
| **User Interaction** | Required 3 times | None |
| **Time** | 2-3 min (with pauses) | 1-2 min (no pauses) |
| **Budget** | 28+24 (retrieval+enrich) | 48+36 (higher for demos) |
| **Max Iterations** | 6 | 10 |
| **Use Case** | Production research | Testing/demo/benchmarking |
| **Control** | User has full control | Fully automated |
| **Output** | Published to UI | Saved to JSON file |

---

**ĐÁP ÁN:**
`SHOWCASE_MODE=true` set `enable_hitl=False` trong `build_test_graph()`, thay thế các `*_node()` functions bằng `*_node_auto()` versions. Các auto versions chỉ return `{"confirmed": True}` hoặc `{"status": "approved"}` mà KHÔNG gọi `interrupt()`, nên không có gate nào pause đợi user approve cả!
