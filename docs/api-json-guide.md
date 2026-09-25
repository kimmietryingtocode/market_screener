# API JSON Guide

This document is for frontend development. The public API is the FastAPI service
under `backend/`, running on `http://localhost:8081` in Docker Compose.

All JSON fields use `snake_case`.

## 1. Health check

```http
GET /healthz
```

Response `200`:

```json
{
  "status": "ok"
}
```

## 2. Authentication flow

### Register

```http
POST /api/v1/auth/register
Content-Type: application/json
```

Request:

```json
{
  "email": "demo@example.com",
  "password": "password123"
}
```

Response `201`:

```json
{
  "token": "<jwt>"
}
```

### Login

```http
POST /api/v1/auth/login
Content-Type: application/json
```

Request:

```json
{
  "email": "demo@example.com",
  "password": "password123"
}
```

Response `200`:

```json
{
  "token": "<jwt>"
}
```

Store the token in the frontend and send it on protected requests:

```http
Authorization: Bearer <jwt>
```

Never send the password to any endpoint other than register or login, and never
include the JWT in a URL query parameter.

## 3. Portfolio flow

### Create a portfolio

```http
POST /api/v1/portfolios
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request:

```json
{
  "name": "Tech Portfolio",
  "holdings": [
    { "ticker": "AAPL", "weight": 0.5 },
    { "ticker": "MSFT", "weight": 0.5 }
  ]
}
```

Rules:

- `name` is required.
- `holdings` must contain at least one item.
- `ticker` is normalized to uppercase by the backend.
- Each `weight` must be greater than `0` and at most `1`.
- The total weight must be between `0.999` and `1.001`.

Response `201`:

```json
{
  "id": 1,
  "name": "Tech Portfolio",
  "created_at": "2026-09-20T12:00:00Z",
  "holdings": [
    {
      "id": 1,
      "portfolio_id": 1,
      "ticker": "AAPL",
      "weight": 0.5
    },
    {
      "id": 2,
      "portfolio_id": 1,
      "ticker": "MSFT",
      "weight": 0.5
    }
  ]
}
```

### List the current user's portfolios

```http
GET /api/v1/portfolios
Authorization: Bearer <jwt>
```

Response `200`:

```json
[
  {
    "id": 1,
    "name": "Tech Portfolio",
    "created_at": "2026-09-20T12:00:00Z",
    "holdings": []
  }
]
```

The current implementation returns portfolio metadata in this list. The detail
and analytics routes can be expanded without changing the list contract.

### Get portfolio analytics

```http
GET /api/v1/portfolios/{portfolio_id}/analytics
Authorization: Bearer <jwt>
```

Response `200`:

```json
{
  "annualized_return": 0.142,
  "volatility": 0.21,
  "sharpe_ratio": 0.68,
  "max_drawdown": -0.18,
  "lookback": "1y"
}
```

The FastAPI backend loads holdings from PostgreSQL and calls the quant service
at `quant:8000`. The browser should call FastAPI, never the quant service
directly.

## 4. Planned stock ranking contract

This route is documented for frontend work but is not implemented in the
current backend. The frontend should not call it until a backend router is
added and the route is included in the API verification flow:

```http
GET /api/v1/rankings/latest
```

Planned response:

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

## 5. Planned backtest contract

Create a backtest job:

```http
POST /api/v1/backtests
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request:

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
{
  "backtest_id": 42,
  "status": "queued"
}
```

Read the result:

```http
GET /api/v1/backtests/42
Authorization: Bearer <jwt>
```

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

The frontend should treat `queued`, `running`, `completed`, and `failed` as
separate UI states.

## 6. Errors

FastAPI currently returns validation errors in its native shape. Examples:

Missing or invalid token, `401`:

```json
{
  "detail": "missing bearer token"
}
```

Invalid portfolio weights, `400`:

```json
{
  "detail": "holding weights must sum to 1.0, got 0.9000"
}
```

Invalid request body, `422`:

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

Do not make the frontend depend on exact validation message text. Use the HTTP
status and show a user-friendly message.

## 7. Frontend request examples

```javascript
const response = await fetch("http://localhost:8081/api/v1/portfolios", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  },
  body: JSON.stringify({
    name: "Tech Portfolio",
    holdings: [
      { ticker: "AAPL", weight: 0.5 },
      { ticker: "MSFT", weight: 0.5 },
    ],
  }),
});

const payload = await response.json();
if (!response.ok) {
  throw new Error(payload.detail ?? "Request failed");
}
```

When a field changes, update this guide, `docs/api-contracts.md`, and the
corresponding Pydantic model together.
