-- Tenancy, billing, shares, notifications, API keys, migration ledger

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

ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS org_id TEXT REFERENCES organizations(id);
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS created_by TEXT REFERENCES users(id);
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

UPDATE research_runs SET org_id = 'org_default' WHERE org_id IS NULL;

CREATE INDEX IF NOT EXISTS research_runs_org_created_idx
    ON research_runs (org_id, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS research_runs_org_status_idx
    ON research_runs (org_id, status)
    WHERE deleted_at IS NULL;

ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS org_id TEXT REFERENCES organizations(id);
ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS created_by TEXT REFERENCES users(id);
UPDATE scenarios SET org_id = 'org_default' WHERE org_id IS NULL;
CREATE INDEX IF NOT EXISTS scenarios_org_created_idx ON scenarios (org_id, created_at DESC);

ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS org_id TEXT REFERENCES organizations(id);
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS created_by TEXT REFERENCES users(id);
UPDATE knowledge_records SET org_id = 'org_default' WHERE org_id IS NULL;
CREATE INDEX IF NOT EXISTS knowledge_records_org_active_idx
    ON knowledge_records (org_id, active, updated_at DESC);

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

INSERT INTO schema_migrations (id) VALUES ('003_tenancy') ON CONFLICT DO NOTHING;
