CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT,
    plan TEXT NOT NULL DEFAULT 'free',
    monthly_run_quota INT NOT NULL DEFAULT 50,
    retention_days INT NOT NULL DEFAULT 90,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    stripe_price_id TEXT,
    billing_status TEXT NOT NULL DEFAULT 'none',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT,
    name TEXT,
    image_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memberships (
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'org:member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX IF NOT EXISTS memberships_user_idx ON memberships (user_id);

INSERT INTO organizations (id, name, slug, plan, monthly_run_quota)
VALUES ('org_default', 'Default Org', 'default', 'free', 50)
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, name)
VALUES ('user_dev', 'dev@localhost', 'Dev User')
ON CONFLICT (id) DO NOTHING;

INSERT INTO memberships (org_id, user_id, role)
VALUES ('org_default', 'user_dev', 'org:admin')
ON CONFLICT DO NOTHING;

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
    org_id TEXT REFERENCES organizations(id),
    created_by TEXT REFERENCES users(id),
    title TEXT,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS research_runs_status_idx ON research_runs (status);
CREATE INDEX IF NOT EXISTS research_runs_created_idx ON research_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS research_runs_org_created_idx
    ON research_runs (org_id, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS research_runs_org_status_idx
    ON research_runs (org_id, status)
    WHERE deleted_at IS NULL;

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
    org_id TEXT REFERENCES organizations(id),
    created_by TEXT REFERENCES users(id),
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
CREATE INDEX IF NOT EXISTS scenarios_org_created_idx ON scenarios (org_id, created_at DESC);
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
    org_id TEXT REFERENCES organizations(id),
    created_by TEXT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS knowledge_records_active_idx
    ON knowledge_records (active, updated_at DESC);
CREATE INDEX IF NOT EXISTS knowledge_records_status_idx
    ON knowledge_records (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS knowledge_records_org_active_idx
    ON knowledge_records (org_id, active, updated_at DESC);

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

CREATE TABLE IF NOT EXISTS run_shares (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    permission TEXT NOT NULL DEFAULT 'read',
    expires_at TIMESTAMPTZ,
    created_by TEXT REFERENCES users(id),
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS run_shares_run_idx ON run_shares (run_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS run_shares_token_idx ON run_shares (token) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS usage_ledger (
    id BIGSERIAL PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    run_id UUID REFERENCES research_runs(id) ON DELETE SET NULL,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'research_run',
    tokens INT NOT NULL DEFAULT 0,
    cost_estimate_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS usage_ledger_org_month_idx
    ON usage_ledger (org_id, created_at DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    run_id UUID REFERENCES research_runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS notifications_user_unread_idx
    ON notifications (user_id, created_at DESC)
    WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS notifications_org_idx
    ON notifications (org_id, created_at DESC);

CREATE TABLE IF NOT EXISTS org_api_keys (
    id UUID PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    scopes TEXT[] NOT NULL DEFAULT ARRAY['research:write']::TEXT[],
    created_by TEXT REFERENCES users(id),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS org_api_keys_org_idx
    ON org_api_keys (org_id)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS product_events (
    id BIGSERIAL PRIMARY KEY,
    org_id TEXT,
    user_id TEXT,
    event_name TEXT NOT NULL,
    props JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS product_events_name_idx ON product_events (event_name, created_at DESC);

INSERT INTO schema_migrations (id) VALUES ('000_init') ON CONFLICT DO NOTHING;
