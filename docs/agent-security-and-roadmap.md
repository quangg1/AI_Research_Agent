# Agent security audit & roadmap

Tài liệu này ghi **kết quả xác minh codebase** (không phải kế hoạch chung chung) cho các mục Tier 0–3. Cập nhật sau khi review `grounding.py`, `fetch.py`, `collector.py`, `docs.py`, `retrieval/store.py`, `knowledge.py`, `llm/client.py`, `eval/`.

Liên quan: [agent-research-system.md](agent-research-system.md)

---

## Tier 0 — Bắt buộc xác minh trước

### 1. Prompt injection từ nội dung fetch / search

#### Câu hỏi

`grounding.py` có chặn instruction injection từ HTML/PDF không? `full_text` có vào LLM prompt mà không lọc không?

#### Kết quả xác minh

| Thành phần | Chặn injection? | Chi tiết |
| --- | --- | --- |
| `grounding.py` | **Không** | Chỉ verify quote overlap + block folklore (`FORBIDDEN` regex). Không liên quan injection. |
| `fetch.sanitize_fetched_content` | **Có (một phần)** | Regex theo **dòng** (`INJECTION_PATTERNS` trong `tools/fetch.py`). Thay dòng khớp bằng `[filtered untrusted instruction]`. |
| Enrich / gap_enrich | **Có** | Gọi `sanitize_fetched_content` sau `evidence_from_url`. |
| Search (Tavily) | **Trước đây: Không** | Snippet gán thẳng vào `evidence[]` (`search.py`). |
| Scholar (OpenAlex) | **Trước đây: Không** | Abstract gán thẳng (`scholar.py`). |
| Docs / corpus upload | **Trước đây: Không** | Snippet từ Qdrant/Postgres — user-uploaded, vẫn là untrusted. |
| Report writer | **Không đủ** | `deep_write.format_research_notes` nhét `quote` / `snippet` / `full_text` vào prompt **không có** fence `untrusted` / least-privilege delimiter. |
| Critic LLM | **Không** | `_llm_critic` gửi title/url/role — rủi ro thấp hơn nhưng vẫn untrusted metadata. |
| `llm/redact.py` | **Không** | Chỉ scrub API keys, không phải injection defense. |

**Kết luận:** Đây là **lỗ hổng nghiêm trọng** so với best practice 2026 (least-privilege: external content = data, không phải instruction). Lớp `sanitize_fetched_content` tồn tại nhưng **không phủ toàn pipeline**; `grounding.py` **không** thay thế được.

#### Mitigation đã thêm (P0)

- `domain/untrusted_content.py` — `sanitize_evidence()`, `wrap_untrusted_text()`, `untrusted_system_rule()`, adversarial eval helpers.
- `collector_node` gọi sanitize **sau** merge search/scholar/docs, **trước** enrich/retrieve/LLM.
- `deep_write.format_research_notes` + `writer_system()` + `compress_system()` — fence `<untrusted_external_source>` + security rule.
- `critic._llm_critic` — evidence excerpts fenced + `untrusted_system_rule` trong system prompt.
- `INJECTION_PATTERNS` mở rộng (`assistant:`, cite-as-verified, mark-as-peer-reviewed).
- `data/eval/adversarial_injection.json` + `eval/adversarial_injection.py` + `tests/test_untrusted_content.py`.

#### Còn thiếu (P1)

| Ưu tiên | Việc |
| --- | --- |
| P1 | Structural fence cho `claim_quote_verify` / `decompose` JSON sidecars ngoài planner path |

**Đã thêm (P1):**

- `domain/injection_guard.py` — NFKC/homoglyph normalize, zero-width strip, paraphrase patterns (EN/VI), `control_plane_system_rule()`, `wrap_user_question()`, `sanitize_gap_slot()`.
- `decompose._llm_slots`, `rewrite_gap_query` / `_llm_gap_query` — control vs untrusted separation; gap evidence fenced; fallback không echo adversarial `followup`.
- `planner._llm_plan`, `hybrid._gemini_rerank` — fenced followups/candidates.
- `critic.followups_for_gaps` — truyền `evidence` vào gap rewrite.

**Test:** `tests/test_injection_guard.py`, `tests/test_fetch_guard.py`, `tests/test_untrusted_content.py`.

---

### 2. Multi-tenant isolation — node `docs` & retrieval loop

#### Câu hỏi

Run tenant A có đọc nhầm corpus tenant B qua Qdrant không? Permission có enforce **trong** retrieval loop không?

#### Kết quả xác minh

| Điểm | Enforce? | Chi tiết |
| --- | --- | --- |
| `docs_node` | **Có** | `org_id = state.get("org_id")` → `get_store_documents(org_id)`, `search_qdrant(..., org_id=org_id)`, `load_corpus(org_id=...)`. |
| Qdrant index | **Có** | Payload `org_scope` = `org_id` hoặc `__global__` (`retrieval/store.py::_index_qdrant`). |
| Qdrant search | **Có** | Filter `should=[global_scope, org_scope]` — tenant chỉ thấy global baseline + corpus riêng. |
| Postgres hydrate | **Có** | Sau Qdrant hit: `WHERE ... AND (org_id IS NULL OR org_id = %s)` khi có `org_id`. |
| `knowledge.lookup` | **Có** | `load_records(org_id)` filter `org_id = %s` (Postgres) hoặc memory filter. |
| `runtime` execution | **Có** | Payload Nest truyền `org_id` vào `ResearchState`. |
| `retrieve_node` | **Đã sửa** | Trước đây `search_qdrant(query, k=...)` **không** truyền `org_id` → chỉ search global index, thiếu org corpus ở bước retrieve; đã patch truyền `org_id`. |
| Run không có `org_id` | **Rủi ro dev** | `knowledge.load_records(None)` không filter org — chỉ chấp nhận được ở dev; production phải luôn có `org_id`. |
| `evidence_graph` persist | **Run-scoped** | `research_sources` keyed by `run_id`, không share cross-run; **không** có institutional memory giữa runs (xem Tier 3 #8). |

**Kết luận:** Isolation **có thiết kế đúng hướng** (enforce trong loop, không chỉ API gateway). Global corpus **cố ý** shared. Gap chính đã fix: `retrieve_node` thiếu `org_id`. Cần test integration Qdrant + regression tenancy.

**Test hiện có:** `tests/test_corpus_tenancy.py` (knowledge + memory store + Qdrant org_scope mock).

#### Đã thêm

- `domain/tenancy.py` — `missing_org_guard()` + `REQUIRE_ORG_ID` env (`briefing_node` fail-closed).
- Test Qdrant org marker isolation (mock `search_qdrant` filter).

---

## Tier 1 — Đòn bẩy cao, rủi ro thấp

### 3. Model tiering theo node

#### Xác minh

`llm/client.py` dùng `settings.gemini_model` mặc định; **đã có** per-role override qua `llm/roles.py`:

| Env | Role |
| --- | --- |
| `GEMINI_MODEL_PLANNER` | planner |
| `GEMINI_MODEL_CRITIC` | critic, rerank |
| `GEMINI_MODEL_REPORT` | report writer, race_write, integrity sidecar |

Planner/critic/report gọi `use_role_model()` trước `generate` / `generate_json`. Rỗng = fallback `GEMINI_MODEL`.

### 4. Mở rộng eval

#### Xác minh

`data/eval/golden_set.json` + `eval/runner.py` — routing, folklore, coverage gate.

**Đã thêm:**

- `data/eval/citation_gold.json` + `eval/citation_benchmark.py` + `tests/test_citation_benchmark.py`
- `data/eval/adversarial_injection.json` + `eval/adversarial_injection.py`
- `tests/test_cost_regression.py` — enrich cap ceiling per iteration

---

## Tier 2 — Đổi kiến trúc vừa phải

### 5. Parallel subagent theo slot

#### Xác minh

Song song theo **tool type** (`search ∥ scholar ∥ docs`). Slots từ `derive_slots()` đi **chung** collector → enrich → extract.

**Đánh giá:** Chỉ parallelize slot độc lập (mechanism, comparison, scalability). Giữ quantitative + contradiction tập trung (`quant_reconcile` đúng hướng).

### 6. DAG per-slot + budget slot-aware

#### Xác minh

Budget **global** (`max_retrieval_calls`, `max_enrich_calls`). `DEEP_RESERVE_CALLS` reserve cứng iter 1.

**Đã thêm (nhẹ):** `domain/slot_budget.py` — weighted `slot_enrich_quota()`, `reclaim_enrich_cap()`, `adaptive_retrieval_reserve()` (planner iter-1), `simulate_enrich_waste()` + skew tests trong `test_cost_regression.py`. Kết quả fixture lệch tải: quota ~100% giảm misallocation vs global; reclaim bổ sung khi slot covered còn quota dư.

---

## Tier 3 — Mở rộng sản phẩm

### 7. Memo → structured decision object

#### Xác minh

Pipeline dừng ở `memo_gate`. Output: `body_markdown` + `claims[]` + `citations[]` + **`decision_payload`** (`domain/decision_export.py`) — JSON máy đọc được (claims, citations, coverage, confidence).

### 8. Evidence graph tái sử dụng giữa runs

#### Xác minh

| Cơ chế | Scope | Reuse evidence? |
| --- | --- | --- |
| `state.evidence[]` | Per run | Không |
| `knowledge.py` | Reuse **memo** + `seed_evidence` |
| `domain/evidence_cache.py` | Org-scoped verified quant rows + TTL (`EVIDENCE_CACHE_TTL_DAYS`); fail-closed khi `REQUIRE_ORG_ID` |
| `persist_evidence_graph` | Per run — không lookup cross-run |

---

## Priority matrix (tóm tắt)

| ID | Mục | Trạng thái | Hành động tiếp |
| --- | --- | --- | --- |
| T0-1 | Injection defense | ✅ Mostly | Homoglyph patterns; planner/decompose fence |
| T0-2 | Tenant isolation | ✅ | CI Qdrant integration; audit upload JWT path |
| T1-3 | Model tiering | ✅ | Set `GEMINI_MODEL_*` in prod |
| T1-4 | Citation eval | ✅ | Mở rộng gold set; CI wiring |
| T2-5 | Slot parallel | ❌ | Chỉ slot độc lập |
| T2-6 | Slot budget | ✅ Mostly | Per-slot used tracking at runtime |
| T3-7 | Decision object | ✅ | `Report.decision_payload` |
| T3-8 | Cross-run evidence | ✅ | TTL + org scope; planner seed lookup |

---

## File map (security-relevant)

| File | Vai trò |
| --- | --- |
| `app/tools/fetch.py` | `INJECTION_PATTERNS`, `sanitize_fetched_content` |
| `app/domain/untrusted_content.py` | Sanitize + fence + adversarial eval |
| `app/llm/roles.py` | Per-role model overrides |
| `app/domain/tenancy.py` | `REQUIRE_ORG_ID` guard |
| `app/domain/decision_export.py` | `decision_payload` builder |
| `app/domain/evidence_cache.py` | Cross-run verified quant cache (in-memory) |
| `app/domain/slot_budget.py` | Per-slot enrich quota hints |
| `app/report/deep_write.py` | Writer prompts + untrusted fence |
| `tests/test_fetch_guard.py` | Injection unit tests |
| `tests/test_untrusted_content.py` | Fence + adversarial eval |
| `tests/test_security_tiers.py` | Tenancy + model roles |
| `tests/test_corpus_tenancy.py` | Knowledge/store/Qdrant org isolation |
