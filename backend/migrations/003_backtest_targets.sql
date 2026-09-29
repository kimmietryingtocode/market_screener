-- Structured target-weight schedule and worker job-contract columns.
--
-- worker/entrypoint.py already defines a file-based job contract (job_id,
-- execution_state, research_outcome, input_hashes, validation_messages,
-- available_at, methodology_version). These columns let a `backtests` row
-- round-trip through that contract instead of the worker inventing a
-- second, parallel status model.
--
-- backtest_targets is the canonical, queryable source for the target-weight
-- schedule that worker/persistence.py exports to the CSV the C++ engine
-- reads. Note the identifier mismatch this table exists to resolve: here
-- security_id is a real bigint FK, but the exported CSV's own
-- `security_id` column must hold the ticker STRING (see normalize_price_csv.py
-- / make_test_targets.py) because that's what the C++ engine matches against
-- price rows. worker/persistence.py is the only place that translation happens.

ALTER TABLE backtests
    ADD COLUMN IF NOT EXISTS job_id TEXT UNIQUE,
    ADD COLUMN IF NOT EXISTS research_outcome TEXT
        CHECK (research_outcome IN ('collecting', 'pending', 'blocked', 'completed')),
    ADD COLUMN IF NOT EXISTS methodology_version TEXT,
    ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS input_hashes JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS validation_messages JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS backtest_targets (
    id             BIGSERIAL PRIMARY KEY,
    backtest_id    BIGINT NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    security_id    BIGINT NOT NULL REFERENCES securities(id) ON DELETE RESTRICT,
    signal_at      TIMESTAMPTZ NOT NULL,
    execution_date DATE NOT NULL,
    target_weight  NUMERIC(6,5) NOT NULL CHECK (target_weight >= 0 AND target_weight <= 1),
    provenance_id  TEXT NOT NULL,
    UNIQUE (backtest_id, security_id, execution_date)
);

CREATE INDEX IF NOT EXISTS idx_backtest_targets_backtest
    ON backtest_targets(backtest_id, execution_date);
