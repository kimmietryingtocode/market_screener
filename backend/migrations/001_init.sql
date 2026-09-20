-- 001_init.sql
-- Core schema: users, portfolios, holdings.
-- FastAPI owns user and portfolio persistence. Quant workers write market data
-- and score tables from the follow-up migration.

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE portfolios (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, name)
);

CREATE TABLE holdings (
    id            BIGSERIAL PRIMARY KEY,
    portfolio_id  BIGINT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker        TEXT NOT NULL,
    weight        NUMERIC(6,5) NOT NULL CHECK (weight >= 0 AND weight <= 1),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(portfolio_id, ticker)
);

CREATE INDEX idx_portfolios_user_id ON portfolios(user_id);
CREATE INDEX idx_holdings_portfolio_id ON holdings(portfolio_id);

-- Cached analytics results (this is what Redis will front later —
-- the table is the source of truth, Redis is just a hot cache on top of it).
CREATE TABLE analytics_runs (
    id            BIGSERIAL PRIMARY KEY,
    portfolio_id  BIGINT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    result_json   JSONB NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_analytics_runs_portfolio_id ON analytics_runs(portfolio_id);
