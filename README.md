# Kiln — Evidence-Backed Decision Research for LLM Systems

[![CI](https://github.com/quangg1/AI_Research_Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/quangg1/AI_Research_Agent/actions/workflows/ci.yml)

**Kiln** (`AI_Research_Agent`) is a deep-research agent stack that turns high-stakes LLM-systems questions — serving economics, RAG architecture, agent design, evaluation — into **cited decision memos** with coverage gates, contradiction analysis, and human approval checkpoints.

It is **not** a general-purpose chatbot. Every run is a bounded LangGraph pipeline with tool budgets, falsifiable routing rules, and post-generation integrity checks.

### Tóm tắt (VI)

**Kiln** là nền tảng nghiên cứu sâu (deep research) cho quyết định hệ thống LLM: agent Python/LangGraph + API NestJS + Web React, kèm Postgres / Redis / Qdrant. Pipeline có ngân sách tool, cổng kiểm tra coverage, và memo có trích dẫn.

- **Chạy nhanh (không clone tay):** dùng one-liner bên dưới — script tự shallow-clone vào thư mục quản lý (`~/.kiln/...`), tạo `.env`, rồi `docker compose up`.
- **Yêu cầu:** Docker Desktop.
- **Auth local:** `AUTH_MODE=dev` (header `X-Dev-Org-Id=org_default`) — mở UI và gửi câu hỏi nghiên cứu.
- **Nhánh hiện tại:** `cursor/fix-kiln-memo-quality-4dd5` (README/quickstart đẩy trên nhánh này; GitHub mặc định có thể vẫn là `main` — merge khi sẵn sàng).

---

## Features (high level)

| Area | What you get |
|------|----------------|
| **Research agent** | LangGraph pipeline: brief → plan → multi-source retrieval → critic loops → cited memo |
| **API** | NestJS BFF, BullMQ jobs, SSE progress, org tenancy |
| **Web** | React + Vite UI (research, workspace, corpus, scenarios) |
| **Data** | PostgreSQL 16 (runs, checkpoints, evidence), Redis (queue), Qdrant (corpus vectors) |
| **Quality** | Coverage gates, claim–quote checks, folklore blocking, Trust Bench eval harnesses |

```
Web (React :5173) ──► API (NestJS :3000) ──► Agent (FastAPI/LangGraph :8000)
                           │                        │
                        Redis                    Postgres + Qdrant
```

---

## Prerequisites

- **Docker Desktop** (Docker Engine + Compose v2)
- Optional but recommended for real runs: at least one LLM API key (`GOOGLE_API_KEY` / `OPENAI_API_KEY` / `XAI_API_KEY`) and `TAVILY_API_KEY` for web search

---

## Quick start — no manual clone (primary)

The quickstart script creates a managed install directory, shallow-clones this repo, copies `.env.example` → `.env` (safe local demo defaults: `AUTH_MODE=dev`), and starts the stack.

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/quangg1/AI_Research_Agent/cursor/fix-kiln-memo-quality-4dd5/scripts/quickstart.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/quangg1/AI_Research_Agent/cursor/fix-kiln-memo-quality-4dd5/scripts/quickstart.ps1 | iex
```

Or download-and-run:

```powershell
curl.exe -fsSL https://raw.githubusercontent.com/quangg1/AI_Research_Agent/cursor/fix-kiln-memo-quality-4dd5/scripts/quickstart.ps1 -o quickstart.ps1
powershell -ExecutionPolicy Bypass -File .\quickstart.ps1
```

### What you get

| Service | URL |
|---------|-----|
| **Web UI** | http://localhost:5173 |
| API health | http://localhost:3000/health |
| Agent health | http://localhost:8000/health |

Default install path: `~/.kiln/AI_Research_Agent` (override with `KILN_HOME` / `$env:KILN_HOME`).

Pin a branch/tag:

```bash
KILN_REF=main curl -fsSL https://raw.githubusercontent.com/quangg1/AI_Research_Agent/cursor/fix-kiln-memo-quality-4dd5/scripts/quickstart.sh | bash
```

```powershell
$env:KILN_REF = "main"; irm https://raw.githubusercontent.com/quangg1/AI_Research_Agent/cursor/fix-kiln-memo-quality-4dd5/scripts/quickstart.ps1 | iex
```

> **Note:** First run **builds** images from source (several minutes). After the [publish-ghcr](.github/workflows/publish-ghcr.yml) workflow has published images, quickstart will prefer `ghcr.io/quangg1/kiln-*` pulls when available.

### Add API keys (for real research)

Edit the generated `.env` in the install directory, set e.g. `GOOGLE_API_KEY=...` (and ideally `TAVILY_API_KEY=...`), then:

```bash
cd ~/.kiln/AI_Research_Agent
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate agent api
```

### Submit a research run

1. Open http://localhost:5173
2. Dev auth attaches `X-Dev-User-Id` / `X-Dev-Org-Id=org_default` automatically
3. Enter a research question → confirm briefing / plan gates → wait for the cited memo

Example questions: *Fine-tune weekly runbooks vs RAG for a 50k-chunk corpus?* · *Self-host 8B FP8 vs 70B API at fixed QPS?*

---

## Alternative: managed shallow clone (no pipe-to-shell)

```bash
mkdir -p ~/.kiln && cd ~/.kiln
git clone --depth 1 --branch cursor/fix-kiln-memo-quality-4dd5 https://github.com/quangg1/AI_Research_Agent.git AI_Research_Agent
cd AI_Research_Agent
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Windows (PowerShell):

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.kiln" | Out-Null
Set-Location "$env:USERPROFILE\.kiln"
git clone --depth 1 --branch cursor/fix-kiln-memo-quality-4dd5 https://github.com/quangg1/AI_Research_Agent.git AI_Research_Agent
Set-Location AI_Research_Agent
Copy-Item .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

> Pure `docker compose -f https://raw.githubusercontent.com/...` without a checkout usually fails here because build contexts and volume mounts need the repo files locally — that is why quickstart manages a shallow clone for you.

---

## Full local-dev clone (secondary)

```bash
git clone https://github.com/quangg1/AI_Research_Agent.git
cd AI_Research_Agent
git checkout cursor/fix-kiln-memo-quality-4dd5   # or main after merge
cp .env.example .env
# edit .env — set LLM + search keys
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Useful Make targets: `make up`, `make down`, `make logs`, `make test`, `make eval`.

### Agent-only (no full stack)

```bash
cd apps/agent
pip install -e ".[dev]"
pytest -q
python -m app.cli "Does RAG always require a vector database?"
```

---

## Environment variables

See **[`.env.example`](.env.example)** for the full list. Quickstart copies it to `.env` with safe local defaults.

| Variable | Purpose | Local demo default |
|----------|---------|-------------------|
| `AUTH_MODE` / `VITE_AUTH_MODE` | `dev` / `clerk` / `disabled` | `dev` |
| `ALLOW_DEV_AUTH` | Allow `X-Dev-*` headers | `true` |
| `GOOGLE_API_KEY` / `OPENAI_API_KEY` / `XAI_API_KEY` | LLM providers | empty (set for real runs) |
| `TAVILY_API_KEY` | Web search | empty (recommended) |
| `S2_API_KEY` | Semantic Scholar (rate limits) | empty |
| `WEB_PORT` | Host port for UI | `5173` |
| `DATABASE_URL` / `REDIS_URL` / `QDRANT_URL` | Infra (compose overrides hosts inside containers) | localhost URLs in example |
| `AGENT_SHARED_KEY` / `API_*_KEY` | Service-to-service | empty OK for local demo |
| `CLERK_*` / `STRIPE_*` | Production auth/billing | empty |

**Do not commit** a filled `.env`. Shared keys and Stripe/Clerk secrets stay local.

---

## Ports

| Service | Host port | Notes |
|---------|-----------|-------|
| Web | **5173** | Nginx serves SPA and proxies `/v1/` → API |
| API | **3000** | Published via `docker-compose.dev.yml` |
| Agent | **8000** | Health: `/health` |
| Postgres | 5432 | Dev overlay |
| Redis | 6379 | Dev overlay |
| Qdrant | 6333 / 6334 | Dev overlay |

Production-style `docker-compose.yml` alone publishes only the web port; quickstart always includes `docker-compose.dev.yml` so API/agent health checks are reachable on the host.

---

## Prebuilt images (GHCR) — optional

Workflow: [`.github/workflows/publish-ghcr.yml`](.github/workflows/publish-ghcr.yml)  
Override: [`docker-compose.ghcr.yml`](docker-compose.ghcr.yml)

```bash
docker compose -f docker-compose.yml -f docker-compose.ghcr.yml -f docker-compose.dev.yml pull
docker compose -f docker-compose.yml -f docker-compose.ghcr.yml -f docker-compose.dev.yml up -d
```

Images: `ghcr.io/quangg1/kiln-agent`, `kiln-api`, `kiln-web`.

**Caveat:** Until the workflow has run successfully and packages are public (or you `docker login ghcr.io`), pulls fail and quickstart **falls back to local build**. Trigger via Actions → *publish-ghcr* → *Run workflow* after merge/push.

---

## Architecture (short)

| Layer | Tech | Role |
|-------|------|------|
| Web | React, Vite, TypeScript | Research UI, workspace, corpus |
| API | NestJS, BullMQ | Auth, tenancy, jobs, SSE |
| Agent | Python 3.12, FastAPI, LangGraph | Research graph + memo writer |
| Postgres | 16 | Runs, events, checkpoints, evidence |
| Redis | 7 | Job queue |
| Qdrant | 1.13 | Corpus embeddings |

Repo layout: `apps/agent`, `apps/api`, `apps/web`, `packages/contracts`, `infra/postgres`, `data/corpus`, `docs/`.

Deeper docs: [docs/agent-research-system.md](docs/agent-research-system.md) · [docs/ops.md](docs/ops.md) · [docs/deploy-render.md](docs/deploy-render.md) · [docs/deploy-oracle.md](docs/deploy-oracle.md)

---

## Status / branch notes

- Active development branch for memo-quality work: **`cursor/fix-kiln-memo-quality-4dd5`** (this README and quickstart scripts live here).
- GitHub’s default branch may still be **`main`**. Visitors who land on `main` without this README should switch branches or merge this branch when ready.
- CD today: Render auto-deploy from `main` (see `render.yaml`). GHCR publish is additive for local/quickstart pulls.

---

## License

No `LICENSE` file is published in the repository yet. Third-party APIs (Clerk, Stripe, Tavily, LLM providers) require their own account keys and terms.

---

## Design principles (for reviewers)

1. **Budgeted pipeline** — split retrieval/enrich pools; critic respects remaining iterations.
2. **Rules outside the graph** — business logic in `domain/`; nodes orchestrate.
3. **Critic is authoritative** — coverage slots trump model “sufficient” claims.
4. **Three-source retrieval** — web, papers, and corpus.
5. **Human gates** — brief, plan, and memo before publish.
6. **Org-scoped knowledge** — no cross-tenant corpus leakage.