# Target Architecture

## Ownership

```text
React frontend
    -> backend/ FastAPI public API
        -> PostgreSQL (source of truth)
        -> Redis (cache, job state, pub/sub)
        -> quant/ FastAPI quant service
        -> worker/ Python ingestion and scoring jobs
        -> backtest/ C++ engine adapter
```

The public backend is FastAPI. New product work belongs in `backend/`, `worker/`, `quant/`, and `backtest/`.

## Repository boundaries

| Directory   | Owns                                                                | Must not own                             |
| ----------- | ------------------------------------------------------------------- | ---------------------------------------- |
| `backend/`  | Public HTTP API, auth, authorization, persistence orchestration     | Quant formulas, direct provider scraping |
| `quant/`    | Stateless market calculations and portfolio analytics               | Users, JWT, PostgreSQL writes            |
| `screener/` | Existing sector/industry/company momentum pipeline                  | Public API concerns                      |
| `worker/`   | Scheduled or long-running ingestion, scoring, and job orchestration | Browser-facing routes                    |
| `backtest/` | C++ backtest engine contract and implementation                     | Authentication, database credentials     |

## Data flow

```text
Market provider (Yahoo/FMP/CSV)
    -> worker ingestion
    -> validate and normalize
    -> PostgreSQL price_bars/fundamentals
    -> worker feature and score calculation
    -> PostgreSQL scores
    -> backend FastAPI
    -> React frontend
```

## Backtest flow

```text
POST /api/v1/backtests
    -> backend creates a job and returns job_id
    -> worker loads point-in-time prices and scores
    -> worker invokes the C++ engine through the adapter contract
    -> worker stores metrics and equity curve
    -> Redis publishes progress
    -> frontend reads GET /api/v1/backtests/{id}
```

The C++ engine receives data files or structured input and returns structured output. It does not connect to PostgreSQL or Redis.

## Implementation order

1. Finish FastAPI auth and portfolio parity in `backend/`.
2. Add `securities`, `price_bars`, `fundamental_snapshots`, and `scores` migrations.
3. Move ingestion/scoring orchestration under `worker/` while reusing `screener/pipeline` calculations.
4. Expose the C++ engine through the backtest worker.
5. Switch the frontend to the FastAPI port 8081.
