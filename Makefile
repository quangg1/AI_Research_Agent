.PHONY: up down logs seed test eval agent-dev api-dev web-dev oracle-up oracle-logs

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f agent api

oracle-up:
	docker compose -f docker-compose.yml -f docker-compose.oracle.yml up -d --build

oracle-logs:
	docker compose -f docker-compose.yml -f docker-compose.oracle.yml logs -f caddy web api agent

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f agent api

agent-dev:
	cd apps/agent && python -m uvicorn app.main:app --reload --port 8000

api-dev:
	cd apps/api && npm run start:dev

web-dev:
	cd apps/web && npm run dev

seed:
	cd apps/agent && python -m app.retrieval.ingest

test:
	cd apps/agent && python -m pytest -q

eval:
	cd apps/agent && python -m app.eval.runner
