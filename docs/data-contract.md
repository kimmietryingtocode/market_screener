# Data and Service Contract

This document defines the boundary between the backtest wrapper, worker,
FastAPI backend, PostgreSQL, and frontend. It is separate from the public API
examples because the backtest service consumes normalized research data rather
than raw Yahoo, SEC, or FactSet payloads.

## Ownership

| Component        | Accepts                              | Produces                        | Owns persistence            |
| ---------------- | ------------------------------------ | ------------------------------- | --------------------------- |
| Ingestion/worker | Provider data and source files       | Validated normalized records    | PostgreSQL writes           |
| Backtest wrapper | Validated prices and target schedule | Run status and result artifacts | No canonical DB writes      |
| FastAPI backend  | User requests and job results        | Stable public JSON              | API orchestration and reads |
| Frontend         | Public FastAPI JSON                  | User interactions               | None                        |
| PostgreSQL       | Normalized records and run results   | Queryable canonical data        | Yes                         |
| Redis            | Job state and cache entries          | Temporary state                 | No canonical data           |

The browser calls FastAPI only. The backtest wrapper must not own auth, browser
concerns, or direct database credentials.

## Backtest wrapper input

The first local wrapper may use CSV files. A later HTTP wrapper can use the same
fields as JSON. The required logical inputs are:

### Prices

| Field                          | Type     | Required | Rule                                  |
| ------------------------------ | -------- | -------- | ------------------------------------- |
| `trading_date`                 | ISO date | Yes      | One row per security and date         |
| `ticker`                       | string   | Yes      | Uppercase, must exist in `securities` |
| `adjusted_close`               | decimal  | Yes      | Greater than zero                     |
| `open`, `high`, `low`, `close` | decimal  | No       | Must be non-negative when present     |
| `volume`                       | integer  | No       | Must be non-negative when present     |

Duplicate `(ticker, trading_date)` rows, null required fields, and malformed
dates are rejected. The wrapper must not silently fill missing prices.

### Target schedule

| Field            | Type     | Required | Rule                                            |
| ---------------- | -------- | -------- | ----------------------------------------------- |
| `signal_at`      | ISO date | Yes      | The date the decision was available             |
| `execution_date` | ISO date | Yes      | Must not precede `signal_at`                    |
| `security_id`    | string   | Yes      | Must exist in the supplied price table/universe |
| `target_weight`  | decimal  | Yes      | Between `0` and `1`                             |
| `provenance_id`  | string   | Yes      | Identifies the model/data decision              |

The C++ engine's target CSV uses `security_id`; the current price CSV uses
`ticker`. The worker is responsible for resolving the stable security identity
before creating the target file. The schedule must not use information
published after `signal_at`. Target weights for one execution date must satisfy
the configured portfolio and cash rules. Missing target rows mean no decision;
they do not automatically mean liquidation.

### Universe and fundamentals

The universe identifies valid securities and may include `ticker`,
`company_name`, `sector`, and `industry`. Fundamental records must include
`period_end`, `filing_date`, `data_source`, and the metric values used by the
model. `filing_date` is the availability date used for point-in-time checks;
`period_end` must not be used as a substitute for it.

## Wrapper output

For a successful run, the wrapper returns a result envelope and writes artifacts
to the requested output directory:

```json
{
  "status": "completed",
  "run_id": "bt_20260924_001",
  "summary": {
    "total_return": 0.124,
    "annualized_return": 0.187,
    "volatility": 0.214,
    "sharpe_ratio": 0.81,
    "max_drawdown": -0.196
  },
  "artifacts": [
    "summary.csv",
    "equity_curve.csv",
    "account_ledger.csv",
    "trades.csv",
    "holdings.csv",
    "rebalance_log.csv",
    "statistics_notes.csv",
    "run_config.csv"
  ]
}
```

These are files produced by the current C++ engine. `summary.csv` contains the
headline portfolio and benchmark metrics. `equity_curve.csv` supports charts,
`trades.csv` records executed transactions, `holdings.csv` records daily
positions, and `account_ledger.csv` supports cash and transaction-cost
reconciliation. `rebalance_log.csv` preserves the target decisions that were
executed; `statistics_notes.csv` defines metric calculations; and
`run_config.csv` records the parameters needed to reproduce the run.

The wrapper may return `queued`, `running`, `completed`, or `failed`. A failed
run must identify the category and field when possible:

```json
{
  "status": "failed",
  "error": {
    "code": "INVALID_PRICE_DATA",
    "message": "duplicate ticker and trading_date",
    "field": "prices[12]"
  }
}
```

The wrapper output is not automatically a public API response. The worker maps
validated results into PostgreSQL, and FastAPI maps stored results into the
contract in `docs/api-contracts.md`.

## Database mapping

The canonical schema is `backend/migrations/002_market_data.sql`.

| Input/output concept       | PostgreSQL table        | Key rule                                                |
| -------------------------- | ----------------------- | ------------------------------------------------------- |
| Security master            | `securities`            | Unique `ticker`                                         |
| Daily prices               | `price_bars`            | Unique `(security_id, trading_date)`                    |
| Point-in-time fundamentals | `fundamental_snapshots` | Preserve `period_end` and `filing_date`                 |
| Model scores               | `scores`                | Unique security/date/methodology                        |
| Job metadata               | `backtests`             | Status is `queued`, `running`, `completed`, or `failed` |
| Summary metrics            | `backtest_results`      | One row per backtest                                    |
| Daily performance          | `equity_curve_points`   | Unique backtest/date                                    |
| Explanations               | `explanations`          | Unique security/date/model                              |

PostgreSQL is the source of truth. Redis may track a running job but must not
be the only place a completed result exists.

## Public API mapping

The frontend uses only the public endpoints documented in
`docs/api-json-guide.md`:

- rankings read from `scores` joined to `securities`
- backtest status and metrics read from `backtests` and `backtest_results`
- equity chart data read from `equity_curve_points`
- portfolio analytics are calculated by the quant service and returned by
  FastAPI

The ranking endpoint is currently a planned contract. It must not be presented
as available until its router, database query, response schema, and API test
are implemented together.

## Change rule

When a field changes, update this document, the matching Pydantic schema, the
public API documentation, and the relevant database migration or mapping test.
