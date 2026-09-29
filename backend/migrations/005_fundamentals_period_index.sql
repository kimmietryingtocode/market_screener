-- worker/calculate_scores.py picks the latest snapshot per security with
-- DISTINCT ON (security_id) ORDER BY security_id, period_end DESC. The
-- existing idx_fundamentals_available is on (security_id, filing_date),
-- which doesn't serve that ordering.

CREATE INDEX IF NOT EXISTS idx_fundamentals_latest_period
    ON fundamental_snapshots(security_id, period_end DESC);
