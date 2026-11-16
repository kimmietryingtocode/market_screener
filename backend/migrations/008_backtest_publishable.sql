-- `status` answers "did the job run"; it is not the same question as "is this
-- result trustworthy enough to show a user". The worker already computes a
-- publishable flag from data-coverage checks (worker/coverage.py), but it was
-- only ever written to the job result file, so the API could not tell a client
-- whether a completed backtest was a trusted result or a diagnostic one.

ALTER TABLE backtests
    ADD COLUMN IF NOT EXISTS publishable BOOLEAN NOT NULL DEFAULT FALSE;
