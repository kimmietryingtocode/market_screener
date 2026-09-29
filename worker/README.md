# Worker Boundary

This directory owns long-running and scheduled jobs. It is not a public HTTP API.

Implemented:

```text
worker/ingest_prices.py         yfinance -> validate/normalize -> price_bars (upsert)
worker/ingest_fundamentals.py   yfinance -> fundamental_snapshots + securities metadata
worker/calculate_scores.py      price_bars + fundamental_snapshots -> scores (percentile ranks)
worker/run_backtest.py          invokes the C++ engine, writes CSV outputs
worker/build_targets.py         portfolio holdings -> dated backtest_targets schedule
worker/persistence.py           backtests/backtest_targets <-> CSV <-> backtest_results/equity_curve_points
worker/run_pending_backtests.py polls backtests WHERE status='queued', runs each end to end
```

`worker/ingest_fundamentals.py` **estimates** filing_date (period_end + 45
days) because yfinance doesn't publish one, and tags every row
`data_source='yfinance-estimated-filing-date'`. Those rows power the
rankings surface ("what do we know now?"). They must not be used for
point-in-time backtest gating ("what was knowable on date X?") — that still
belongs to `backtest/fundamentals/`, which carries real EDGAR accessions and
filing dates. A backtest reading fundamental_snapshots should filter
data_source explicitly.

`worker/ingest_prices.py` reuses `download_history`/`normalize_price_bars`
from `screener/ingest_prices.py` (per the reuse rule below) and adds the
Postgres upsert; `screener/ingest_prices.py` itself still also writes a local
CSV snapshot, kept for the screener's own use.

`worker/run_backtest.py` invokes the C++ engine with historical prices and a
dated target schedule. The engine writes summary, trades, holdings, and equity
curve CSV outputs.

`docker-compose.yml` runs both loops as long-lived services, sharing the
`worker/Dockerfile` image (its `ENTRYPOINT` is just `python`; each service
supplies its own `command`):

```text
price-ingestor         python -m worker.ingest_prices --interval-seconds 86400
fundamentals-ingestor  python -m worker.ingest_fundamentals --interval-seconds 86400
score-calculator       python -m worker.calculate_scores --interval-seconds 86400
backtest-worker        python -m worker.run_pending_backtests --poll-interval 30
```

`docker compose up` starts both automatically. Each loop catches and logs
exceptions per iteration instead of exiting, so one bad ticker or one failed
backtest doesn't take the whole service down; `restart: unless-stopped`
covers anything that still escapes that (e.g. a lost DB connection).

Worker rules:

- PostgreSQL is the durable source of truth.
- Redis stores job status and cache entries, not canonical market history.
- Providers are external and can fail; validate responses and record errors.
- Every fundamental record needs `filing_date` to prevent look-ahead bias.
- Jobs must be idempotent: rerunning the same date should not duplicate rows.
