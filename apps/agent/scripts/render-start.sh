#!/bin/sh
set -eu
# Apply base schema + numbered migrations (idempotent IF NOT EXISTS).
# Render free Postgres starts empty — compose's migrate service is not used there.

if [ -z "${DATABASE_URL:-}" ]; then
  echo "migrate: DATABASE_URL unset, skipping"
else
  URL=$(printf '%s' "$DATABASE_URL" | sed 's#^postgres:#postgresql:#')
  echo "migrate: applying /app/migrations/*.sql"
  for f in /app/migrations/*.sql; do
    [ -f "$f" ] || continue
    echo "migrate: $f"
    psql "$URL" -v ON_ERROR_STOP=1 -f "$f"
  done
  echo "migrate: done"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
