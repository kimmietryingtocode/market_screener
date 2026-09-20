# Worker Boundary

This directory owns long-running and scheduled jobs. It is not a public HTTP API.

Planned jobs:

```text
worker/ingest_prices.py       provider -> normalized price_bars
worker/ingest_fundamentals.py provider -> point-in-time fundamentals
worker/calculate_scores.py    prices + fundamentals -> scores
worker/run_backtest.py        database inputs -> C++ engine -> results
```

The current working price ingestion script is still `screener/ingest_prices.py`.
It writes a local snapshot to `data/price_bars.csv`, which is ignored by Git.
Move orchestration here after the PostgreSQL migrations are added; reuse the
pure calculations in `screener/pipeline/` instead of copying them.

`worker/run_backtest.py` invokes the C++ engine with historical prices and a
dated target schedule. The engine writes summary, trades, holdings, and equity
curve CSV outputs.

Worker rules:

- PostgreSQL is the durable source of truth.
- Redis stores job status and cache entries, not canonical market history.
- Providers are external and can fail; validate responses and record errors.
- Every fundamental record needs `filing_date` to prevent look-ahead bias.
- Jobs must be idempotent: rerunning the same date should not duplicate rows.
