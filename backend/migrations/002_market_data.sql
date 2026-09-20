-- Market data and quant result schema.
-- The Python worker writes these tables; FastAPI reads them for public API responses.

CREATE TABLE IF NOT EXISTS securities (
    id            BIGSERIAL PRIMARY KEY,
    ticker        TEXT NOT NULL UNIQUE,
    company_name  TEXT,
    sector        TEXT,
    industry      TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS price_bars (
    security_id      BIGINT NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
    trading_date     DATE NOT NULL,
    open             NUMERIC(20,8),
    high             NUMERIC(20,8),
    low              NUMERIC(20,8),
    close            NUMERIC(20,8),
    adjusted_close   NUMERIC(20,8) NOT NULL,
    volume           BIGINT,
    PRIMARY KEY (security_id, trading_date)
);

CREATE INDEX IF NOT EXISTS idx_price_bars_date ON price_bars(trading_date);

CREATE TABLE IF NOT EXISTS fundamental_snapshots (
    id                 BIGSERIAL PRIMARY KEY,
    security_id        BIGINT NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
    period_end         DATE NOT NULL,
    filing_date        DATE NOT NULL,
    revenue            NUMERIC(24,8),
    revenue_growth     NUMERIC(12,8),
    earnings_growth    NUMERIC(12,8),
    operating_margin   NUMERIC(12,8),
    free_cash_flow     NUMERIC(24,8),
    debt_to_equity     NUMERIC(12,8),
    data_source        TEXT NOT NULL,
    UNIQUE (security_id, period_end, filing_date, data_source)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_available
    ON fundamental_snapshots(security_id, filing_date);

CREATE TABLE IF NOT EXISTS scores (
    id                    BIGSERIAL PRIMARY KEY,
    security_id           BIGINT NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
    score_date            DATE NOT NULL,
    momentum_score        NUMERIC(8,4) NOT NULL,
    fundamental_score     NUMERIC(8,4) NOT NULL,
    composite_score       NUMERIC(8,4) NOT NULL,
    components             JSONB NOT NULL DEFAULT '{}'::jsonb,
    methodology_version   TEXT NOT NULL,
    UNIQUE (security_id, score_date, methodology_version)
);

CREATE INDEX IF NOT EXISTS idx_scores_latest
    ON scores(score_date, composite_score DESC);

CREATE TABLE IF NOT EXISTS backtests (
    id                    BIGSERIAL PRIMARY KEY,
    user_id               BIGINT REFERENCES users(id) ON DELETE SET NULL,
    status                TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    start_date            DATE NOT NULL,
    end_date              DATE NOT NULL,
    benchmark             TEXT NOT NULL,
    rebalance_frequency   TEXT NOT NULL,
    parameters             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at           TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id                    BIGSERIAL PRIMARY KEY,
    backtest_id           BIGINT NOT NULL UNIQUE REFERENCES backtests(id) ON DELETE CASCADE,
    total_return          NUMERIC(14,8),
    annualized_return     NUMERIC(14,8),
    volatility             NUMERIC(14,8),
    sharpe_ratio          NUMERIC(14,8),
    max_drawdown          NUMERIC(14,8),
    alpha_vs_benchmark    NUMERIC(14,8)
);

CREATE TABLE IF NOT EXISTS equity_curve_points (
    id                    BIGSERIAL PRIMARY KEY,
    backtest_id           BIGINT NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    trading_date          DATE NOT NULL,
    portfolio_value       NUMERIC(24,8) NOT NULL,
    benchmark_value       NUMERIC(24,8) NOT NULL,
    UNIQUE (backtest_id, trading_date)
);

CREATE TABLE IF NOT EXISTS explanations (
    id                    BIGSERIAL PRIMARY KEY,
    security_id           BIGINT NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
    score_date            DATE NOT NULL,
    summary               TEXT NOT NULL,
    strengths              JSONB NOT NULL DEFAULT '[]'::jsonb,
    risks                  JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_name            TEXT NOT NULL,
    generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (security_id, score_date, model_name)
);