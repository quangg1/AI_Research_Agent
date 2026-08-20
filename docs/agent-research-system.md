# Hệ thống agent research

Kiln không phải chatbot. Agent là một pipeline có ngân sách, critic, HITL và citation — chạy trong `apps/agent`, được Nest enqueue từ ngoài.

Tài liệu này mô tả tư duy thiết kế, các file, và liên kết giữa chúng. Mở trên GitHub/GitLab sau khi push; không phụ thuộc Cursor Canvas.

---

## Tư duy thiết kế

User hỏi một quyết định LLM-systems. Hệ thống phải trả memo có claim, quote, contradiction — hoặc nói out of scope. Folklore bị chặn, không được khuyến nghị.

### Sáu nguyên tắc trong code

| Nguyên tắc | Hiện ra ở đâu | Vì sao |
| --- | --- | --- |
| Pipeline có budget, không loop vô hạn | `schema.Budget` + `after_critic` | LLM/search tốn tiền; `max_iterations` / tool_calls / tokens |
| Luật tách khỏi control flow | `domain/` vs `graph/nodes/` | pytest được grounding, coverage, routing mà không cần FastAPI |
| Critic là cổng cứng, không tin LLM | `critic.py` + `coverage.py` | LLM nói sufficient vẫn fail nếu thiếu slot `must_answer` |
| Ba nguồn, một collector | search / scholar / docs → collector | Web, paper, docs nội bộ; node không chọn thì return ngay |
| Người duyệt hai lần | briefing + hitl interrupt | Chốt brief trước khi tốn search; chốt memo trước khi xong |
| Falsifiable | `data/eval/golden_set.json` + `eval/runner.py` | Routing và folklore phải đo được, không chỉ demo |

### Nouns graph mang theo

| Noun | Ý nghĩa |
| --- | --- |
| **Plan** | `query_type`, `agents_to_run`, `sub_queries`. Planner viết, node search đọc. |
| **Evidence** | url, snippet, tier, credibility. Gộp unique ở collector, rank ở retrieve. |
| **Report** | claims + citations + `body_markdown`. `verify_claims` trước khi lưu knowledge. |

---

## Pipeline (một lần chạy)

```
START
  → briefing
  → planner
  → search ∥ scholar ∥ docs
  → collector
  → enrich
  → retrieve
  → extract
  → critic
  → HITL  (hoặc loop lại planner)
  → report
END
```

| Node | File | Việc | Rẽ |
| --- | --- | --- | --- |
| briefing | `graph/nodes/briefing.py` | Chốt ResearchBrief (goal, depth, must_answer) | out_of_scope → report; không thì planner |
| planner | `graph/nodes/planner.py` | Phân loại, budget, cache knowledge, chọn agent | cached → report; không thì 3 nguồn |
| search | `graph/nodes/search.py` | Web (Tavily / DDG) | join collector |
| scholar | `graph/nodes/scholar.py` | OpenAlex papers | join collector |
| docs | `graph/nodes/docs.py` | Corpus nội bộ | join collector |
| collector | `graph/nodes/collector.py` | Gộp evidence, trừ budget tool calls | → enrich |
| enrich | `graph/nodes/enrich.py` | Fetch full page | → retrieve |
| retrieve | `retrieve_node` trong `collector.py` | Hybrid rank + Qdrant | → extract |
| extract | `graph/nodes/extract.py` | Quote + claim seed | → critic |
| critic | `graph/nodes/critic.py` | Coverage + contradiction + followup | đủ → hitl; thiếu + còn budget → planner |
| hitl | `graph/nodes/hitl.py` | `interrupt(approve_report)` | revise → planner; approve → report |
| report | `graph/nodes/report.py` | Compose memo, grounding, save knowledge | END |

**Adaptive skip:** `after_planner` luôn trả cả search, scholar, docs để join ở collector ổn định. Node không nằm trong `agents_to_run` return ngay — không gọi tool.

Topology chỉ nằm `graph/builder.py`. Node không gọi nhau.

---

## Bốn lớp file

Phụ thuộc một chiều: node được gọi domain. Domain không được import graph. Tools không biết HITL.

| Lớp | Folder | Được import bởi | Cấm |
| --- | --- | --- | --- |
| Boundary | `main.py`, `contracts.py`, `runtime.py`, `cli.py` | HTTP / CLI / Nest | Luật citation trong FastAPI |
| Control flow | `graph/builder.py`, `state.py`, `nodes/` | runtime | SQL org, Clerk |
| Luật | `domain/` | nodes, eval, report | FastAPI, LangGraph interrupt |
| Adapter | `llm/`, `tools/`, `retrieval/`, `persistence/` | nodes + runtime | Quyết định out_of_scope |

### `domain/` — mỗi file một luật

| File | Luật |
| --- | --- |
| `schema.py` | Plan, Budget, Claim, Report, SourceTier |
| `routing_policy.py` | Phân loại query, `heuristic_plan`, out_of_scope |
| `research_intent.py` | goal, mechanism query, topic leakage |
| `knowledge.py` | Lookup / save memo đã nghiên cứu |
| `coverage.py` | must_answer slots, `critic_should_pass` |
| `grounding.py` | FORBIDDEN folklore + `verify_claims` |
| `citations.py` | Ledger, quote-in-source |
| `credibility.py` | Host → tier → score |

`eval/runner.py` import thẳng domain, không import `builder.py`. Luật phải test được độc lập.

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
| `runtime.py` | `graph/builder.py` compile | checkpointer Postgres |
| `runtime.py` | nodes (qua graph) | `ResearchState` patch từng bước |
| `planner_node` | `domain/knowledge`, `routing_policy`, llm | `reuse_mode` + `agents_to_run` |
| search / scholar / docs | `tools/` + `retrieval/` | list evidence |
| collector → retrieve | `retrieval/hybrid.py`, `store.py` | ranked retrieved |
| extract | `domain/citations` | quotes / claim seeds |
| `critic_node` | `domain/coverage` + llm | `CriticVerdict` + followups |
| `hitl_node` | LangGraph `interrupt` | payload ra Nest/UI, đợi resume |
| `report_node` | grounding, citations, `report/compose`, `knowledge.save` | Report + metrics |
| `runtime._snapshot_dict` | NDJSON frame | AgentSnapshot về API |

**State là bus.** Mọi node nhận `ResearchState`, trả dict patch. Rẽ nhánh chỉ nằm `builder.py` (`after_planner`, `after_critic`, `after_hitl`). Đổi topology: sửa builder, không sửa `search.py`.

---

## File nguồn chính

- `apps/agent/app/graph/builder.py`
- `apps/agent/app/graph/state.py`
- `apps/agent/app/graph/nodes/`
- `apps/agent/app/domain/`
- `apps/agent/app/runtime.py`
- `apps/agent/app/main.py`
- `apps/agent/app/eval/runner.py`
- `packages/contracts/src/index.ts` (`AgentSnapshotSchema`)
