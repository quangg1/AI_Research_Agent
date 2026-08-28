FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends curl postgresql-client \
  && rm -rf /var/lib/apt/lists/*

COPY apps/agent/pyproject.toml /app/pyproject.toml
COPY apps/agent/app /app/app
RUN pip install --no-cache-dir .

COPY data/scenarios/prices.json /app/data/scenarios/prices.json

# Empty Render Postgres needs base schema before 001_* ALTERs.
COPY infra/postgres/init.sql /app/migrations/000_init.sql
COPY infra/postgres/migrations/001_research_lifecycle_owner.sql /app/migrations/001_research_lifecycle_owner.sql
COPY infra/postgres/migrations/002_knowledge_corpus.sql /app/migrations/002_knowledge_corpus.sql
COPY infra/postgres/migrations/003_tenancy.sql /app/migrations/003_tenancy.sql
COPY infra/postgres/migrations/004_evidence_graph.sql /app/migrations/004_evidence_graph.sql
COPY infra/postgres/migrations/005_parent_run.sql /app/migrations/005_parent_run.sql

COPY apps/agent/scripts/render-start.sh /app/render-start.sh
RUN chmod +x /app/render-start.sh

EXPOSE 8000
# Render injects PORT; local compose still works via same entrypoint.
CMD ["/app/render-start.sh"]
