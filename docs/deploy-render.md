# Deploy Kiln on Render (CI/CD)

GitHub Actions run tests on every push/PR (`.github/workflows/ci.yml`).
Render **auto-deploys** services when `main` updates (Blueprint `autoDeploy: true`).

## One-time setup

1. Push this repo to GitHub (already: `https://github.com/quangg1/AI_Research_Agent`).
2. Open the Blueprint deeplink (fills from `render.yaml` on `main`):

   [https://dashboard.render.com/blueprint/new?repo=https://github.com/quangg1/AI_Research_Agent](https://dashboard.render.com/blueprint/new?repo=https://github.com/quangg1/AI_Research_Agent)

3. Connect the GitHub repo if prompted → **Apply**.
4. Fill secrets marked `sync: false` in the Blueprint UI (same values for shared keys on api + agent):
   - `AGENT_SHARED_KEY` (and optional `API_TO_AGENT_KEY`) — long random string
   - LLM / Tavily keys (or leave empty and rely on BYOK in the UI)
5. After first deploy, copy public URLs from the dashboard:
   - Agent → set **kiln-api** env `AGENT_BASE_URL=https://<kiln-agent>.onrender.com`
   - API → set **kiln-web** env `VITE_API_URL=https://<kiln-api>.onrender.com`
   - API → set `CORS_ORIGINS=https://<kiln-web>.onrender.com`
6. Redeploy **kiln-api** and **kiln-web** (Manual Deploy → Clear build cache & deploy for web so Vite picks up `VITE_API_URL`).

## Database migrations

Compose applies `infra/postgres/migrations/*.sql` locally. On Render, run once against `kiln-postgres` (Dashboard → PostgreSQL → **PSQL** / Shell), in order:

```bash
# From a machine with psql and DATABASE_URL (External Database URL from Render):
for f in infra/postgres/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

Or paste each file into the Render PSQL console.

## CI vs CD

| Piece | What runs |
| --- | --- |
| **CI** | GitHub Actions: api tests/build, web typecheck/tests/build, agent pytest |
| **CD** | Render watches `main` → rebuilds kiln-web / kiln-api / kiln-agent |

No deploy token required for CD if the Blueprint is linked to the GitHub repo.

## Free-tier caveats

- Free web services **spin down** after ~15 minutes idle (cold start ~30–60s).
- Free Postgres **expires after ~30 days**.
- No Qdrant service — agent uses Postgres fallback for retrieval.
- For always-on / commercial use, upgrade instance plans and Postgres.

## Local parity

Prefer `docker compose up --build` for day-to-day. Oracle Always Free path remains in [deploy-oracle.md](deploy-oracle.md).
