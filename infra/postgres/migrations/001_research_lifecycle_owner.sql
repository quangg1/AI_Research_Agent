BEGIN;

ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS execution_version INT NOT NULL DEFAULT 1;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS execution_id UUID;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS current_job_id TEXT;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE run_events ADD COLUMN IF NOT EXISTS execution_id UUID;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS attempt INT NOT NULL DEFAULT 1;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS sequence INT NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS run_events_execution_sequence_idx
    ON run_events (run_id, execution_id, attempt, sequence)
    WHERE execution_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS research_dispatch_outbox (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    execution_id UUID NOT NULL,
    execution_version INT NOT NULL,
    job_name TEXT NOT NULL,
    payload JSONB NOT NULL,
    dispatched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempts INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS research_dispatch_pending_idx
    ON research_dispatch_outbox (created_at, id) WHERE dispatched_at IS NULL;

COMMIT;
