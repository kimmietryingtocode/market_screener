-- Lets a backtest be created from a saved portfolio's holdings via the API,
-- instead of only being seeded by hand for engine smoke tests.

ALTER TABLE backtests
    ADD COLUMN IF NOT EXISTS portfolio_id BIGINT REFERENCES portfolios(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_backtests_user_id ON backtests(user_id);
CREATE INDEX IF NOT EXISTS idx_backtests_status ON backtests(status);
