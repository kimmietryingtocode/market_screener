# API Contracts

All public endpoints are served by `backend/` FastAPI. JSON field names use `snake_case`.

## Authentication

### Register

`POST /api/v1/auth/register`

```json
{ "email": "demo@example.com", "password": "password123" }
```

Response `201`:

```json
{ "token": "<jwt>" }
```

### Login

`POST /api/v1/auth/login`

Request is the same as register. Response:

```json
{ "token": "<jwt>" }
```

Protected endpoints send:

```text
Authorization: Bearer <jwt>
```

## Rankings

This endpoint is a planned public contract. It is not implemented in the
current backend yet.

`GET /api/v1/rankings/latest`

```json
{
  "score_date": "2026-09-20",
  "benchmark": "SPY",
  "methodology_version": "v1",
  "stocks": [
    {
      "rank": 1,
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "sector": "Technology",
      "composite_score": 84.2,
      "momentum_score": 91.0,
      "fundamental_score": 74.1
    }
  ]
}
```

## Backtests

### Create job

`POST /api/v1/backtests`

```json
{
  "start_date": "2023-01-01",
  "end_date": "2025-12-31",
  "benchmark": "SPY",
  "rebalance_frequency": "monthly",
  "number_of_stocks": 10,
  "momentum_weight": 0.6,
  "fundamental_weight": 0.4,
  "initial_capital": 100000
}
```

Response `202`:

```json
{ "backtest_id": 42, "status": "queued" }
```

### Get result

`GET /api/v1/backtests/{backtest_id}`

```json
{
  "backtest_id": 42,
  "status": "completed",
  "metrics": {
    "total_return": 0.684,
    "annualized_return": 0.187,
    "volatility": 0.214,
    "sharpe_ratio": 0.81,
    "max_drawdown": -0.196,
    "alpha_vs_benchmark": 0.043
  },
  "equity_curve": [
    {
      "date": "2023-01-03",
      "portfolio_value": 100000,
      "benchmark_value": 100000
    }
  ]
}
```

## AI explanation

`GET /api/v1/stocks/{ticker}/explanation`

```json
{
  "ticker": "AAPL",
  "score_date": "2026-09-20",
  "summary": "AAPL ranks highly because its momentum is strong and profitability is healthy.",
  "strengths": ["Strong relative strength", "Healthy operating margin"],
  "risks": ["Average volatility"],
  "model_name": "llm-model",
  "generated_at": "2026-09-20T12:30:00Z"
}
```

## Error shape

The current backend uses FastAPI's native error responses. Clients should use
the HTTP status and should not depend on the exact message text.

Validation or application error:

```json
{
  "detail": "holding weights must sum to 1.0, got 0.9000"
}
```

Request validation error:

```json
{
  "detail": [
    {
      "loc": ["body", "holdings"],
      "msg": "List should have at least 1 item after validation, not 0",
      "type": "too_short"
    }
  ]
}
```

Do not rename fields casually. Change this document and the Pydantic schemas together.
