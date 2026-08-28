# Deploy Kiln on Render (CI/CD)

GitHub Actions run tests on every push/PR (`.github/workflows/ci.yml`).
Render **auto-deploys** services when `main` updates (Blueprint `autoDeploy: true`).

## One-time setup

1. Push this repo to GitHub (already: `https://github.com/quangg1/AI_Research_Agent`).
2. Open the Blueprint deeplink (fills from `render.yaml` on `main`):

   [https://dashboard.render.com/blueprint/new?repo=https://github.com/quangg1/AI_Research_Agent](https://dashboard.render.com/blueprint/new?repo=https://github.com/quangg1/AI_Research_Agent)

3. Connect the GitHub repo if prompted → **Apply**.
4. Fill remaining secrets marked `sync: false`:
   - `REDIS_URL` — reuse your existing free Key Value (Internal Redis URL on **both** kiln-api and kiln-agent)
   - LLM / Tavily keys (optional if using BYOK in the UI)
5. Blueprint wires shared agent config automatically:
   - `AGENT_SHARED_KEY` — generated on **kiln-agent**; **kiln-api** copies it via `fromService`
   - `AGENT_BASE_URL` — auto-wired from kiln-agent hostname (api prepends `https://`)
   - Do **not** set `API_TO_AGENT_KEY` on kiln-api unless you want a separate override (common cause of 401)
6. After first deploy, set public URLs:
   - **kiln-web** → `VITE_API_URL=https://<kiln-api>.onrender.com`
   - **kiln-api** → `CORS_ORIGINS=https://<kiln-web>.onrender.com`
7. Redeploy **kiln-web** (Clear build cache) and **kiln-api**.

## Troubleshooting `invalid agent credentials`

1. **kiln-api** → Environment → **delete** `API_TO_AGENT_KEY` if it exists.
2. Copy `AGENT_SHARED_KEY` from **kiln-agent** → paste into **kiln-api** `AGENT_SHARED_KEY` (must match exactly).
3. **Manual Deploy** kiln-api (env changes do not apply until redeploy).
4. Open `https://<kiln-api>.onrender.com/ready` — if `dependencies` contains `agent_auth`, keys still mismatch.
5. Test agent directly (PowerShell):

```powershell
$key = "PASTE_FROM_kiln-agent_ENV"
$host = "https://YOUR-AGENT.onrender.com"
Invoke-RestMethod "$host/health"
Invoke-WebRequest "$host/ready" -Headers @{"X-Agent-Key"=$key}
```

Last command must return **200**. If it does but the app still fails, redeploy kiln-api.

## Database migrations

Agent Docker image applies `infra/postgres/init.sql` + `migrations/*.sql` on every start
(`apps/agent/scripts/render-start.sh`). Safe to re-run (`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`).

If you need to run them manually against Render Postgres:

```bash
# External Database URL from Render → kiln-postgres
for f in infra/postgres/init.sql infra/postgres/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

## CI vs CD

| Piece | What runs |
| --- | --- |
| **CI** | GitHub Actions: api tests/build, web typecheck/tests/build, agent pytest |
| **CD** | Render watches `main` → rebuilds kiln-web / kiln-api / kiln-agent |

No deploy token required for CD if the Blueprint is linked to the GitHub repo.

## Free-tier caveats

- Free web services **spin down** after ~15 minutes idle (cold start ~30–60s).
- Free Postgres **expires after ~30 days**.
- Free Key Value: **max 1 per workspace** — Blueprint does not create Redis; wire `REDIS_URL` to the existing instance.
- No Qdrant service — agent uses Postgres fallback for retrieval.
- For always-on / commercial use, upgrade instance plans and Postgres.

## Local parity

Prefer `docker compose up --build` for day-to-day. Oracle Always Free path remains in [deploy-oracle.md](deploy-oracle.md).
