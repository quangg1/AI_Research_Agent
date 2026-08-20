# Ops runbook (MVP)

See [docs/deploy-oracle.md](deploy-oracle.md) for Always Free VM + `.io.vn` + Gemini + `AUTH_MODE=dev`.

## Backups

```bash
docker compose exec -T postgres pg_dump -U kiln kiln > backup-$(date +%Y%m%d).sql
```

Restore:

```bash
cat backup-YYYYMMDD.sql | docker compose exec -T postgres psql -U kiln -d kiln
```

## Migrations

Compose runs `infra/postgres/migrations/*.sql` via the `migrate` service on startup.
`003_tenancy.sql` adds organizations, memberships, org-scoped runs, shares, usage, notifications, and API keys.
`schema_migrations` records applied migration ids when present.

## Auth modes

| Mode | Use |
| --- | --- |
| `dev` | Local headers `X-Dev-User-Id`, `X-Dev-Org-Id`, `X-Dev-Role` |
| `clerk` | Browser Bearer JWT from Clerk + optional `X-Org-Id` |
| Org API key | `Authorization: Bearer kiln_…` or `X-API-Key` |

Production refuses `dev` / `disabled`. Nginx no longer injects a shared browser API key.

## SLOs (targets)

- API `/health` availability: 99% monthly
- Research job terminal success (completed or awaiting_human, not failed): track via `research_runs.status`
- Correlation id: echoed as `x-correlation-id` on every response
