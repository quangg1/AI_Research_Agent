# Hệ thống agent research

Kiln không phải chatbot. Agent là pipeline có ngân sách tách pool, critic, HITL, citation integrity — chạy trong `apps/agent`, được Nest enqueue từ ngoài.

Tài liệu này mô tả topology, budget, depth policy, và luồng report writer. Cập nhật theo `graph/builder.py`, `domain/research_depth.py`, `domain/retrieval_limits.py`.

---

## Tư duy thiết kế

User hỏi một quyết định LLM-systems. Hệ thống trả memo có claim, quote, contradiction — hoặc nói out of scope. Folklore bị chặn, không được khuyến nghị.

### Sáu nguyên tắc trong code

| Nguyên tắc | Hiện ra ở đâu | Vì sao |
| --- | --- | --- |
| Pipeline có budget, không loop vô hạn | `schema.Budget` + `after_critic` | LLM/search tốn tiền; `max_iterations` / tokens / **hai pool tool calls** |
| Luật tách khỏi control flow | `domain/` vs `graph/nodes/` | pytest được grounding, coverage, routing mà không cần FastAPI |
| Critic là cổng cứng, không tin LLM | `critic.py` + `coverage.py` | LLM nói sufficient vẫn fail nếu thiếu slot `must_answer` |
| Ba nguồn, một collector | search / scholar / docs → collector | Web, paper, docs nội bộ; node không chọn thì return ngay |
| Người duyệt ba lần | briefing + plan_gate + memo_gate (+ hitl) | Chốt brief → plan → memo trước khi publish |
| Falsifiable | `data/eval/golden_set.json` + `eval/runner.py` + `eval/graph_routing.py` | Routing, graph gates, folklore — không cần live LLM |

### Coverage gate (HITL / memo)

`domain/coverage_gate.py` gán `gate_reason` trên critic:

| `gate_reason` | Ý nghĩa |
| --- | --- |
| `sufficient` | Must-answer đủ |
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
| **Evidence** | url, snippet, tier, credibility, optional `full_text`. Gộp ở collector; rank ở retrieve; enrich bổ sung full text. |
| **Report** | claims + citations + `body_markdown`. `verify_claims` + `fact_lite` + `report_integrity` trước khi lưu knowledge. |

---

## Pipeline (một lần chạy)

```
START
  → briefing
  → planner
  → plan_gate          (HITL: duyệt plan trước khi search)
  → search ∥ scholar ∥ docs
  → collector
  → enrich
  → retrieve
  → extract
  → critic
  → hitl               (approve / revise → planner)
  → report
  → memo_gate          (duyệt memo; revise → critic)
END
```

| Node | File | Việc | Rẽ |
| --- | --- | --- | --- |
| briefing | `graph/nodes/briefing.py` | ResearchBrief (goal, must_answer, depth=deep) | out_of_scope / cancel → report |
| planner | `graph/nodes/planner.py` | Classify, budget pools, knowledge reuse, falsification sub-queries | cached → report; else plan_gate |
| plan_gate | `graph/nodes/plan_gate.py` | Interrupt: user chỉnh plan / scholar textarea | cancel → report; ok → fan-out |
| search | `graph/nodes/search.py` | Tavily / DDG; rank `retrieval_rank_score` | join collector |
| scholar | `graph/nodes/scholar.py` | OpenAlex (+ Semantic Scholar fallback); ưu tiên snippet có benchmark số | join collector |
| docs | `graph/nodes/docs.py` | Corpus nội bộ + Qdrant | join collector |
| collector | `graph/nodes/collector.py` | Gộp evidence; charge **retrieval** pool | → enrich |
| enrich | `graph/nodes/enrich.py` | Full-page fetch; charge **enrich** pool; slot-aware gap URLs | → retrieve |
| retrieve | `collector.retrieve_node` | Hybrid rank + Qdrant | → extract |
| extract | `graph/nodes/extract.py` | Quote + claim seed; micro-extract nếu còn retrieval budget | → critic |
| critic | `graph/nodes/critic.py` | Coverage + contradiction + followup | sufficient / hết budget → hitl; else → planner |
| hitl | `graph/nodes/hitl.py` | `interrupt(approve_report)` | revise → planner; approve → report |
| report | `graph/nodes/report.py` | LLM memo (race/deep write) hoặc `compose` fallback | integrity gap → planner; else memo_gate |
| memo_gate | `graph/nodes/memo_gate.py` | Interrupt duyệt memo cuối | revise → critic |

**Adaptive skip:** `after_plan_gate` luôn fan-out `search`, `scholar`, `docs` khi có agent trong plan. Node không nằm trong `agents_to_run` return ngay — không gọi tool.

**Integrity re-loop:** `report` có thể set `status=integrity_research` → `after_report` quay lại `planner` (thêm retrieval theo `report_integrity`).

Topology chỉ nằm `graph/builder.py`. Node không gọi nhau.

---

## Report writer stack

| Layer | File | Việc |
| --- | --- | --- |
| Notes | `report/deep_write.py` | `format_research_notes`, `method_notes_for_writer`, compress |
| Generation | `report/race_write.py` | Section-wise deep (phase 1 Analysis → phase 2 back matter), expansion, rewrite |
| Fallback | `report/compose.py` | Deterministic memo khi LLM off / fail |
| Post-process | `report/memo_structure.py` | Dedupe Contradictions, merge Metric gaps → Uncertainties, gộp Source quality cites `[1, 6, 8, 9 peer]`, lọc row định tính trong Quantitative table |
| Polish | `race_write.polish_citations` | `merge_inline_citations`, strip `---`, bỏ Visual summary appendix |

Deep memo target ~5500 words (`word_target("deep")`). Không còn bắt buộc code/mermaid appendix.

### Retrieval ranking (ưu tiên số đo)

`domain/adversarial.py`:

- `numeric_evidence_score(ev)` — boost `%`, `ms`, `tok/s`, tên benchmark; hạ survey không có số.
- `retrieval_rank_score(ev)` = `authority_score` + `numeric_evidence_score`.

Dùng trong `search._rank_and_filter`, `scholar` sort sau dedupe, `enrich._prioritize_enrich_urls`.

---

## Bốn lớp file

Phụ thuộc một chiều: node được gọi domain. Domain không được import graph. Tools không biết HITL.

| Lớp | Folder | Được import bởi | Cấm |
| --- | --- | --- | --- |
| Boundary | `main.py`, `contracts.py`, `runtime.py`, `cli.py` | HTTP / CLI / Nest | Luật citation trong FastAPI |
| Control flow | `graph/builder.py`, `state.py`, `nodes/` | runtime | SQL org, Clerk |
| Luật | `domain/` | nodes, eval, report | FastAPI, LangGraph interrupt |
| Adapter | `llm/`, `tools/`, `retrieval/`, `persistence/` | nodes + runtime | Quyết định out_of_scope |

### `domain/` — file chính

| File | Luật |
| --- | --- |
| `schema.py` | Plan, Budget (split pools), Claim, Report, ResearchBrief |
| `research_depth.py` | Force deep + `configure_budget_pools` |
| `retrieval_limits.py` | Caps search/scholar/enrich/planner |
| `routing_policy.py` | Phân loại query, `heuristic_plan`, out_of_scope |
| `research_intent.py` | goal, `authority_score`, topic leakage |
| `adversarial.py` | Hypotheses, falsification queries, quantitative extract, source quality bands |
| `knowledge.py` | Lookup / save memo đã nghiên cứu |
| `coverage.py` | must_answer slots, `critic_should_pass` |
| `grounding.py` | FORBIDDEN folklore + `verify_claims` |
| `report_integrity.py` | Contradiction quant vs decision rule; integrity re-loop |
| `gap_enrich.py` | Slot-targeted full-text fetch (enrich pool) |
| `citations.py` | Ledger, quote-in-source |
| `credibility.py` | Host → tier → score |

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
| search / scholar / docs | `tools/` + `retrieval/` | list evidence |
| collector → retrieve | `retrieval/hybrid.py`, `store.py` | ranked retrieved |
| extract | `domain/citations` | quotes / claim seeds |
| `critic_node` | `domain/coverage` + llm | `CriticVerdict` + followups |
| `hitl` / `plan_gate` / `memo_gate` | LangGraph `interrupt` | payload ra Nest/UI, đợi resume |
| `report_node` | `race_write`, `deep_write`, `compose`, `knowledge.save` | Report + metrics |
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

## File nguồn chính

- `apps/agent/app/graph/builder.py`
- `apps/agent/app/graph/state.py`
- `apps/agent/app/graph/nodes/`
- `apps/agent/app/domain/research_depth.py`
- `apps/agent/app/domain/retrieval_limits.py`
- `apps/agent/app/report/race_write.py`
- `apps/agent/app/report/deep_write.py`
- `apps/agent/app/report/memo_structure.py`
- `apps/agent/app/runtime.py`
- `apps/agent/app/main.py`
- `apps/agent/app/eval/runner.py`
- `packages/contracts/src/index.ts` (`AgentSnapshotSchema`)
