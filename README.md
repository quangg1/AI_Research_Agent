# Kiln — Evidence-Backed Decision Research for LLM Systems

**Kiln** is a multi-tenant research platform that turns complex questions about applied AI—serving economics, RAG architecture, agent design, evaluation methodology—into **cited decision memos** with explicit coverage gates, contradiction analysis, and human approval checkpoints.

It is **not** a general-purpose chatbot. Every run is a bounded LangGraph pipeline with tool budgets, falsifiable routing rules, and post-generation integrity checks.

---

## What problem it solves

Teams building LLM-powered products face decisions that are:

- **High stakes** (architecture, cost, compliance)
- **Noisy** (vendor decks, blog folklore, conflicting papers)
- **Moving fast** (docs and benchmarks change quarterly)

Kiln produces memos that separate **verified claims** from **open questions**, cite primary sources, and flag when research budget or evidence coverage is insufficient—so readers know what is solid versus what still needs validation.

---

## Product surfaces

| Surface | Purpose |
|--------|---------|
| **Research** | Full agent pipeline: brief → plan → multi-source retrieval → critic loops → cited memo |
| **Workspace** | Organization-scoped run history, search, pin/archive, follow-up threads |
| **Corpus** | Shared LLM-systems baseline + **per-org uploads** (markdown) indexed for the docs agent |
| **Scenarios** | Deterministic calculators for serving cost and RAG vs fine-tune tradeoffs |
| **Settings** | Billing (Stripe), org API keys, optional bring-your-own LLM key |
| **Share links** | Tokenized read-only access to completed memos |

---

## System architecture

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────────────────────────┐
│  Web (React)│────▶│ API (NestJS)│────▶│ Agent (Python / FastAPI / LangGraph) │
│  Vite + TS  │ SSE │ BullMQ jobs │ NDJSON│ Research graph + report writer      │
└─────────────┘     └──────┬──────┘     └───────────┬──────────────────────────┘
                           │                          │
                    ┌──────▼──────┐            ┌───────▼────────┐
                    │ Redis queue │            │ PostgreSQL     │
                    └─────────────┘            │ runs, events,  │
                                               │ checkpoints,   │
                                               │ evidence graph │
                                               └───────┬────────┘
                                                       │
                                               ┌───────▼────────┐
                                               │ Qdrant         │
                                               │ corpus vectors │
                                               └────────────────┘
```

| Layer | Technology | Responsibility |
|-------|------------|----------------|
| **Web** | React, Vite, TypeScript | Research UI, workspace, corpus upload, Clerk auth bridge |
| **API** | NestJS, BullMQ, Redis | Auth, tenancy, billing, job dispatch, SSE progress |
| **Agent** | Python 3.12, FastAPI, LangGraph | Research graph, LLM orchestration, retrieval, memo generation |
| **Data** | PostgreSQL 16 | Runs, orgs, users, corpus metadata, LangGraph checkpoints, evidence graph |
| **Vectors** | Qdrant | Org-scoped + global corpus embeddings |
| **Contracts** | `@kiln/contracts` (Zod) | Shared API schemas between web and API |

---

## Research pipeline (LangGraph)

Every production run uses **deep** depth with split tool budgets:

| Pool | Cap (production) | Used for |
|------|------------------|----------|
| Retrieval | 28 calls | Web search (Tavily), academic search (OpenAlex), internal docs |
| Enrich | 24 calls | Full-page fetch for quotes and gap filling |
| Iterations | up to 6 | Critic-driven re-planning when coverage gaps remain |

**Showcase / benchmark mode** (`SHOWCASE_MODE=true`) raises caps to 48 + 36 retrieval/enrich and 10 iterations for demo runs.

```
briefing → planner → plan_gate (HITL)
              ↓
    search ∥ scholar ∥ docs
              ↓
         collector → enrich → retrieve → extract
              ↓
           critic ──(gaps + budget)──► planner
              ↓
            hitl (HITL)
              ↓
           report → memo_gate (HITL) → publish
```

### Human-in-the-loop gates

1. **Briefing** — User confirms research goal and must-answer dimensions  
2. **Plan gate** — User approves agent plan before any tool calls  
3. **HITL / memo gate** — User reviews evidence coverage and final memo  

### Coverage gate (hard stop semantics)

The critic assigns a structured `gate_reason` consumed by the UI and report metrics:

| Reason | Meaning |
|--------|---------|
| `sufficient` | Must-answer slots covered; safe to approve |
| `insufficient_coverage` | Gaps remain; pipeline can loop if budget allows |
| `insufficient_budget` | Gaps remain but tool/iteration budget exhausted |
| `contradicted` | Material tensions unresolved in evidence |

The LLM cannot override a failed coverage check—`domain/coverage.py` is a hard gate on top of the critic model output.

### Evidence sources

| Agent | Source | Role |
|-------|--------|------|
| **Search** | Tavily (fallback: DuckDuckGo) | Current web, vendor docs, benchmarks |
| **Scholar** | OpenAlex (+ Semantic Scholar) | Peer-reviewed and preprint literature |
| **Docs** | Curated corpus + org uploads | Internal notes, uploaded domain documents |

Evidence is merged, ranked (authority + numeric benchmark bias), enriched with full text where possible, and passed through quote verification before memo synthesis.

### Report writer

- **Primary:** Section-wise deep generation (`race_write.py`) with RACE-style structure  
- **Fallback:** Deterministic composer when LLM unavailable  
- **Post-process:** Citation deduplication, quantitative table sanitization, contradiction merge (`memo_structure.py`)  
- **Integrity:** Claim–quote verification, folklore blocking, optional re-research loop  

Target memo length for deep runs: ~5,500 words with executive summary, quantitative table, worked example, and decision rules.

---

## Multi-tenancy and security

| Concern | Implementation |
|---------|----------------|
| **Identity** | [Clerk](https://clerk.com) (sign-in, org switcher) or dev-mode headers for local work |
| **Tenancy boundary** | Organization — runs, billing, corpus uploads, knowledge reuse scoped by `org_id` |
| **API auth** | JWT + `X-Org-Id`, or org API keys (`kiln_*`) |
| **Agent isolation** | Execution payload carries `orgId`; corpus/Qdrant/knowledge filtered per org |
| **Billing** | Stripe subscriptions; monthly run quota per org |
| **Secrets** | Visitor LLM keys held in memory only (BYOK); never persisted to Postgres |
| **Service auth** | Shared key between API and agent for internal execution stream |

Database migrations live in `infra/postgres/` (init + numbered migrations through corpus tenancy).

---

## Quality engineering

Kiln is designed to be **testable without live LLM calls** for routing and gate behavior:

| Harness | Command | What it validates |
|---------|---------|-------------------|
| Domain + routing eval | `python -m app.eval.runner` | Query classification, folklore blocking, golden set |
| Graph routing | `python -m app.eval.graph_routing` | Coverage gate transitions (budget vs coverage) |
| RACE proxy metrics | `python -m app.eval.race_bench` | Memo structure and grounding proxies |
| Unit tests | `pytest` (180+ tests across agent) | Budget accounting, coverage, citations, fetch guards |

Golden set: `data/eval/golden_set.json`

**CLI / showcase runner** (no web UI, auto-approves HITL):

```bash
SHOWCASE_MODE=true python -m app.eval.showcase_run "Your research question?"
```

---

## Repository layout

```
AI_Research_Agent/
├── apps/
│   ├── agent/          # LangGraph pipeline, LLM, retrieval, report writer
│   ├── api/            # NestJS BFF, auth, billing, job queue
│   └── web/            # React SPA
├── packages/
│   └── contracts/      # Shared Zod schemas (TypeScript)
├── infra/
│   └── postgres/       # SQL init + migrations
├── data/
│   ├── corpus/         # Global LLM-systems markdown corpus + org uploads
│   └── eval/             # Golden set and fixtures
└── docs/
    ├── agent-research-system.md   # Deep dive: nodes, domain rules, file map
    ├── ops.md                     # Backups, auth modes
    ├── deploy-oracle.md           # Oracle Cloud Always Free
    └── deploy-render.md           # Render.com blueprint
```

---

## Tech stack summary

| Category | Choices |
|----------|---------|
| Language | Python 3.12 (agent), TypeScript (API + web) |
| Agent framework | LangGraph with PostgreSQL checkpointer |
| LLM providers | Google Gemini, OpenAI, xAI Grok (multi-key failover) |
| Search | Tavily, DuckDuckGo |
| Academic | OpenAlex, Semantic Scholar |
| Embeddings | Gemini `text-embedding-004` (hash fallback offline) |
| Queue | BullMQ on Redis |
| Payments | Stripe |
| Auth | Clerk Organizations |
| Containers | Docker Compose (local); Render / Oracle guides for production |

---

## Quick start (local)

### Prerequisites

- Docker and Docker Compose  
- API keys in `.env` (see `.env.example`): at minimum one LLM provider; Tavily recommended for search quality  

### Run the full stack

```bash
cp .env.example .env
# Edit .env: GOOGLE_API_KEY, TAVILY_API_KEY, etc.

docker compose up -d --build agent api web
```

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API health | http://localhost:3000/health |
| Agent health | http://localhost:8000/health |

### Auth modes

| Mode | `.env` | Use case |
|------|--------|----------|
| **Dev** (default) | `AUTH_MODE=dev`, `VITE_AUTH_MODE=dev` | Local development without Clerk |
| **Clerk** | `AUTH_MODE=clerk`, `VITE_AUTH_MODE=clerk` + Clerk keys | Production / demo with real sign-in |

### Agent-only development

```bash
cd apps/agent
pip install -e ".[dev]"
pytest -q
python -m app.eval.runner
python -m app.cli "Does RAG always require a vector database?"
```

After code changes in Docker, rebuild affected services:

```bash
docker compose up -d --build agent api web
```

---

## Example research questions

Questions that showcase Kiln’s strengths—falsifiable, architecture-focused, evidence-rich:

1. *Fine-tune weekly runbooks vs RAG for a 50k-chunk internal corpus?*  
2. *Vector-only RAG vs BM25 + cross-encoder reranker on the same corpus?*  
3. *Self-host 8B FP8 vs 70B API for batch inference at fixed QPS?*  
4. *Can synthetic data agents overcome domain data scarcity without model collapse?*  
5. *Is LLM-as-judge an unbiased ground truth for RAG evaluation?*  

Architectural folklore (e.g. “RAG always needs a vector DB”) should appear as **contradictions or caveats**, not recommendations—the grounding layer enforces this.

---

## Design principles (for reviewers)

1. **Budgeted pipeline, not infinite agent loop** — Split retrieval/enrich pools; critic respects remaining iterations.  
2. **Rules outside the graph** — Business logic in `domain/`; nodes orchestrate; domain is unit-tested without HTTP.  
3. **Critic is authoritative** — Coverage slots trump LLM “sufficient” verdicts.  
4. **Three-source retrieval** — Web, papers, and corpus converge in one evidence set.  
5. **Three approval gates** — Brief, plan, and memo before publish.  
6. **Org-scoped knowledge** — Corpus uploads and answer reuse do not leak across tenants.  
7. **Eval-first** — Golden set and graph routing tests run in CI-friendly harnesses.  

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/agent-research-system.md](docs/agent-research-system.md) | Full pipeline: nodes, budget, domain file map, Nest↔agent wire protocol |
| [docs/ops.md](docs/ops.md) | Operations, backups, auth |
| [docs/deploy-oracle.md](docs/deploy-oracle.md) | Oracle Always Free deployment |
| [docs/deploy-render.md](docs/deploy-render.md) | Render.com deployment |

---

## License

See repository license file. Third-party API usage (Clerk, Stripe, Tavily, LLM providers) requires respective account keys.

---

## Sample output

A full showcase memo on synthetic data and data-generation agents (generated via `showcase_run`) is available at:

- `data/corpus/showcase_synthetic_data.md` — rendered memo  
- `data/corpus/showcase_synthetic_data.json` — run metrics, coverage slots, and structured report  

This demonstrates deep memo structure: executive summary, quantitative table, worked example, contradictions debate, decision rules, and explicit limitation notes when budget gates apply.
