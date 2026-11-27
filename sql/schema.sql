-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Master SOP Documents table
CREATE TABLE IF NOT EXISTS sop_documents (
    id SERIAL PRIMARY KEY,
    sop_code VARCHAR(64) UNIQUE NOT NULL,
    title TEXT NOT NULL,
    category VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Versioned Policy Clauses (Append-Only Audit History)
CREATE TABLE IF NOT EXISTS rule_versions (
    id SERIAL PRIMARY KEY,
    sop_id INT NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE,
    version_num INT NOT NULL DEFAULT 1,
    rule_clause_id VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    embedding VECTOR(768),
    tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED,
    scope VARCHAR(32) NOT NULL DEFAULT 'GLOBAL',
    priority INT NOT NULL DEFAULT 10,
    conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision_rule JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (sop_id, version_num, rule_clause_id),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

-- Indexes for Temporal Hybrid Search & JSONB Condition Filtering
CREATE INDEX IF NOT EXISTS idx_rule_versions_dates ON rule_versions (valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_rule_versions_tsv ON rule_versions USING GIN (tsv);
CREATE INDEX IF NOT EXISTS idx_rule_versions_conditions ON rule_versions USING GIN (conditions);
CREATE INDEX IF NOT EXISTS idx_rule_versions_embedding ON rule_versions USING hnsw (embedding vector_cosine_ops);

-- 3. Audit Log of Evaluated Incidents
CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    ticket_date DATE NOT NULL,
    query_text TEXT NOT NULL,
    decision_json JSONB NOT NULL,
    latency_ms INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);