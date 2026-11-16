ALTER TABLE ai_documents ADD COLUMN IF NOT EXISTS embedding_provider TEXT NOT NULL DEFAULT 'ollama';
ALTER TABLE ai_documents ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'nomic-embed-text:v1.5';
ALTER TABLE ai_documents ADD COLUMN IF NOT EXISTS embedding_dimensions INTEGER NOT NULL DEFAULT 768;
ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'ollama';
ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS data_policy TEXT NOT NULL DEFAULT 'private_allowed';

CREATE INDEX IF NOT EXISTS ai_documents_embedding_provider_idx
    ON ai_documents (embedding_provider, embedding_model, ticker);
