# Kiln

Evidence-backed **decision research** for applied AI / LLM systems — serving, RAG, agents, eval, and fine-tune vs retrieval tradeoffs.

Not a general chatbot. Domain is broad enough to be hard (vendor hype, conflicting papers, moving docs) and still falsifiable via a golden set.

## Product surfaces

| Surface | What it is |
| --- | --- |
| **Research** | LangGraph agent loop with brief gate, critic loop-back, HITL, cited memo |
| **Scenarios** | Deterministic serving-cost and RAG vs fine-tune calculator |
| **Corpus** | Curated LLM-systems docs indexed for the docs agent |
| **Workspace** | Org-scoped run history + Postgres event timeline |
| **Settings** | Plan/usage (Stripe), workspace API keys, bring-your-own model key |

Multi-tenant SaaS: Clerk Organizations for identity/RBAC, Stripe for billing, org-scoped runs.

See [docs/ops.md](docs/ops.md) for backups and auth modes. Agent loop, files, and data flow: [docs/agent-research-system.md](docs/agent-research-system.md). Oracle Always Free (dev auth): [docs/deploy-oracle.md](docs/deploy-oracle.md). Render Blueprint CI/CD: [docs/deploy-render.md](docs/deploy-render.md).

## Graph

```
                    ┌──────────────────────────────┐
                    │         planner              │
                    │  classify + budget + skip    │
                    └──────┬───────────▲───────────┘
           adaptive fan-out│           │ critic / human revise
         ┌────────┬────────┼────────┐  │
         ▼        ▼        ▼        │  │
      search   scholar    docs      │  │
         │        │        │        │  │
         └────────┴────┬───┘        │  │
                       ▼            │  │
                  collector         │  │
                       ▼            │  │
              hybrid retrieve       │  │
                       ▼            │  │
                    critic ─────────┘  │
                       │               │
                       ▼               │
              HITL interrupt ──────────┘
                       │               │
                       ▼
                    report
              claims + citations
              + contradictions
```

Forbidden folklore (blocked in grounding): bigger-model-always-wins, RAG-always-needs-a-vector-DB, fine-tune-always-beats-RAG, LLM-as-judge-is-ground-truth, multi-agent-always-beats-one-agent.

## Stack

| Layer | Choice |
| --- | --- |
| Agent | Python, FastAPI, LangGraph |
| BFF / jobs | NestJS, BullMQ, Redis |
| UI | React + Vite |
| State | PostgreSQL (runs + LangGraph checkpoints + run_events) |
| Vectors | Qdrant (wired into docs/retrieve, in-process hybrid fallback) |
| LLM | Hosted Gemini / OpenAI / Grok from env. Visitor BYOK only after credits run out; keys are never stored. |
| Search | Tavily or DuckDuckGo |
| Scholar | OpenAlex |
| Docs | Curated LLM-systems corpus + hybrid retrieval |

## Quick start

```bash
cp .env.example .env
# Put GOOGLE_API_KEY, OPENAI_API_KEY, and/or XAI_API_KEY in .env for hosted models.
# Visitors paste their own key only if those credits run out.
docker compose up --build
```

- UI: http://localhost:5173
- API: http://localhost:3000/health
- Agent: http://localhost:8000/health

Without Docker (agent only):

```bash
cd apps/agent
pip install -e ".[dev]"
python -m pytest -q
python -m app.eval.runner
python -m app.cli "Does RAG always require a vector database?"
```

## Demo questions

1. Fine-tune weekly runbooks vs RAG?
2. Vector-only RAG vs BM25 + rerank?
3. Self-host 8B FP8 vs 70B API?
4. Is LLM-as-judge unbiased ground truth?

Architectural claims should cite primary docs or papers. Folklore should show up as a **contradiction or caveat**, not a recommendation.
