-- The engine already produces all of this and the import dropped it.
--
-- equity_curve.csv carries six columns; only three were stored, so the
-- drawdown series the engine had already computed was thrown away and the
-- UI could draw a rising equity line but never the underwater curve
-- underneath it.
--
-- summary.csv reports eleven metrics; six were stored. Turnover, costs and
-- average cash weight are what explain a result — a book that sat half in
-- cash underperforms for a reason the metrics alone never show.
--
-- VaR, CVaR and beta are computed once at import from the same series,
-- rather than per request: the inputs cannot change after a run finishes.

ALTER TABLE equity_curve_points
    ADD COLUMN IF NOT EXISTS daily_return NUMERIC(14,8),
    ADD COLUMN IF NOT EXISTS drawdown     NUMERIC(14,8);

ALTER TABLE backtest_results
    ADD COLUMN IF NOT EXISTS var_95              NUMERIC(14,8),
    ADD COLUMN IF NOT EXISTS cvar_95             NUMERIC(14,8),
    ADD COLUMN IF NOT EXISTS beta_vs_benchmark   NUMERIC(14,8),
    ADD COLUMN IF NOT EXISTS total_turnover      NUMERIC(14,8),
    ADD COLUMN IF NOT EXISTS transaction_costs   NUMERIC(24,8),
    ADD COLUMN IF NOT EXISTS average_cash_weight NUMERIC(14,8),
    ADD COLUMN IF NOT EXISTS rebalance_count     INTEGER;
