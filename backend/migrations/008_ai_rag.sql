-- Self-hosted LangChain/LangGraph analysis persistence and pgvector index.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE ai_documents (
    id BIGSERIAL PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    ticker TEXT,
    published_at TIMESTAMPTZ,
    methodology_version TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ai_documents_ticker_idx ON ai_documents (ticker);
CREATE INDEX ai_documents_source_type_idx ON ai_documents (source_type);
CREATE INDEX ai_documents_embedding_idx ON ai_documents USING hnsw (embedding vector_cosine_ops);

CREATE TABLE ai_reports (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('recommendations', 'stock', 'backtest')),
    ticker TEXT,
    backtest_id BIGINT REFERENCES backtests(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    request_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    facts_json JSONB,
    report_json JSONB,
    error TEXT,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX ai_reports_user_idx ON ai_reports (user_id, created_at DESC);
CREATE INDEX ai_reports_status_idx ON ai_reports (status, created_at);

CREATE TABLE ai_report_citations (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES ai_reports(id) ON DELETE CASCADE,
    document_id BIGINT REFERENCES ai_documents(id) ON DELETE SET NULL,
    source_key TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE ai_messages (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES ai_reports(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'completed', 'failed')),
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ai_messages_report_idx ON ai_messages (report_id, created_at);

CREATE TABLE ai_jobs (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES ai_reports(id) ON DELETE CASCADE,
    message_id BIGINT REFERENCES ai_messages(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_at TIMESTAMPTZ,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ai_jobs_queue_idx ON ai_jobs (status, run_after, created_at);
