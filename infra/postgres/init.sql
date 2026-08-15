CREATE TABLE IF NOT EXISTS research_runs (
    id UUID PRIMARY KEY,
    query TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    query_type TEXT,
    thread_id TEXT NOT NULL UNIQUE,
    budget_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB,
    interrupt_payload JSONB,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    monitor_deadline_at TIMESTAMPTZ,
    execution_version INT NOT NULL DEFAULT 1,
    execution_id UUID,
    current_job_id TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    error TEXT,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS research_runs_status_idx ON research_runs (status);
CREATE INDEX IF NOT EXISTS research_runs_created_idx ON research_runs (created_at DESC);

CREATE TABLE IF NOT EXISTS run_events (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_id UUID,
    attempt INT NOT NULL DEFAULT 1,
    sequence INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS run_events_run_idx ON run_events (run_id, id);
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

CREATE TABLE IF NOT EXISTS scenarios (
    id UUID PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    inputs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    outputs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    linked_run_id UUID REFERENCES research_runs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS corpus_documents (
    id TEXT PRIMARY KEY,
    url TEXT,
    title TEXT,
    host TEXT,
    tier TEXT,
    snippet TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_path TEXT,
    chunk_index INT,
    content_hash TEXT,
    quote TEXT,
    credibility DOUBLE PRECISION,
    published TEXT,
    source_kind TEXT,
    generation BIGINT NOT NULL DEFAULT 1,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    embedding_model TEXT,
    indexed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scenarios_created_idx ON scenarios (created_at DESC);
CREATE INDEX IF NOT EXISTS corpus_documents_host_idx ON corpus_documents (host);
CREATE UNIQUE INDEX IF NOT EXISTS corpus_documents_source_chunk_idx
    ON corpus_documents (source_path, chunk_index)
    WHERE source_path IS NOT NULL;
CREATE INDEX IF NOT EXISTS corpus_documents_active_generation_idx
    ON corpus_documents (active, generation);

CREATE TABLE IF NOT EXISTS knowledge_records (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES research_runs(id) ON DELETE SET NULL,
    goal TEXT NOT NULL,
    answer JSONB NOT NULL DEFAULT '{}'::jsonb,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding JSONB,
    embedding_model TEXT,
    fingerprint TEXT[] NOT NULL DEFAULT '{}',
    queries TEXT[] NOT NULL DEFAULT '{}',
    depth_score INT NOT NULL DEFAULT 0,
    reuse_count BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    last_reused_at TIMESTAMPTZ,
    version INT NOT NULL DEFAULT 1,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS knowledge_records_active_idx
    ON knowledge_records (active, updated_at DESC);
CREATE INDEX IF NOT EXISTS knowledge_records_status_idx
    ON knowledge_records (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS corpus_sync_runs (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'running',
    generation BIGINT NOT NULL,
    source_hash TEXT,
    documents_seen INT NOT NULL DEFAULT 0,
    documents_indexed INT NOT NULL DEFAULT 0,
    embedding_model TEXT,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
