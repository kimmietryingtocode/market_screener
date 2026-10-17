-- Workers claim a queued backtest by flipping it to 'running' atomically, so
-- only one worker ever owns it. The cost of claiming up front is that a
-- worker which dies mid-job leaves the row 'running' forever, where nothing
-- retries it. claimed_at turns the claim into a lease: a row held past a
-- timeout is assumed abandoned and returned to the queue.

ALTER TABLE backtests
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_backtests_running_claims
    ON backtests(claimed_at)
    WHERE status = 'running';
