-- Claim / source / span graph for hover locators and citation verification.

CREATE TABLE IF NOT EXISTS research_sources (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT '',
    quality_band TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    content_text TEXT NOT NULL DEFAULT '',
    retrieved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, url)
);
CREATE INDEX IF NOT EXISTS research_sources_run_idx ON research_sources (run_id);

CREATE TABLE IF NOT EXISTS research_spans (
    id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
    quote TEXT NOT NULL,
    locator_kind TEXT NOT NULL DEFAULT 'paragraph',
    locator_label TEXT NOT NULL DEFAULT '',
    page INT,
    char_start INT,
    char_end INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS research_spans_source_idx ON research_spans (source_id);

CREATE TABLE IF NOT EXISTS research_claims (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    claim_key TEXT NOT NULL,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION,
    published TEXT NOT NULL DEFAULT '',
    quality_band TEXT NOT NULL DEFAULT '',
    verification_status TEXT NOT NULL DEFAULT 'pending',
    verification_note TEXT NOT NULL DEFAULT '',
    locator_label TEXT NOT NULL DEFAULT '',
    quote TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, claim_key)
);
CREATE INDEX IF NOT EXISTS research_claims_run_idx ON research_claims (run_id);

CREATE TABLE IF NOT EXISTS research_claim_edges (
    id UUID PRIMARY KEY,
    claim_id UUID NOT NULL REFERENCES research_claims(id) ON DELETE CASCADE,
    source_id UUID REFERENCES research_sources(id) ON DELETE SET NULL,
    span_id UUID REFERENCES research_spans(id) ON DELETE SET NULL,
    relation TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS research_claim_edges_claim_idx ON research_claim_edges (claim_id);

INSERT INTO schema_migrations (id) VALUES ('004_evidence_graph')
ON CONFLICT (id) DO NOTHING;
