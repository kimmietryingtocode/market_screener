-- Freeze the selected holdings when a backtest is requested. Portfolio rows
-- remain editable, while queued and completed runs keep their original input.

CREATE TABLE IF NOT EXISTS backtest_holdings (
    backtest_id BIGINT NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    weight      NUMERIC(6,5) NOT NULL CHECK (weight > 0 AND weight <= 1),
    PRIMARY KEY (backtest_id, ticker)
);
