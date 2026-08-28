#!/bin/sh
set -eu
# Apply base schema + numbered migrations (idempotent IF NOT EXISTS).
# Render free Postgres starts empty — compose's migrate service is not used there.

if [ -z "${DATABASE_URL:-}" ]; then
  echo "migrate: DATABASE_URL unset, skipping"
elif [ "${SKIP_DB_MIGRATE:-}" = "true" ] || [ "${SKIP_DB_MIGRATE:-}" = "1" ]; then
  echo "migrate: skipped (SKIP_DB_MIGRATE)"
else
  URL=$(printf '%s' "$DATABASE_URL" | sed 's#^postgres:#postgresql:#')
  if [ "${FORCE_DB_MIGRATE:-}" != "1" ]; then
    if psql "$URL" -tAc "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='research_runs' LIMIT 1" 2>/dev/null | grep -q 1; then
      echo "migrate: schema present, skipping (set FORCE_DB_MIGRATE=1 to re-run all SQL)"
      SKIP_ALL=1
    fi
  fi
  if [ "${SKIP_ALL:-}" != "1" ]; then
    echo "migrate: applying /app/migrations/*.sql"
    for f in /app/migrations/*.sql; do
      [ -f "$f" ] || continue
      echo "migrate: $f"
      psql "$URL" -v ON_ERROR_STOP=1 -f "$f"
    done
    echo "migrate: done"
  fi
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
