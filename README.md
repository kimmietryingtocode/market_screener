# Market screener and portfolio analysis platform

This project combines momentum and fundamental signals to rank stocks, explain
the score, and evaluate portfolio strategies against a benchmark.

The public backend is FastAPI. Python owns data ingestion and quant
calculations. The backtest engine is C++. PostgreSQL is the source of truth
and Redis is used for cache and job state.

**Status:** FastAPI migration scaffolded, price ingestion verified against
Yahoo Finance, and C++ backtest boundary documented. Frontend ownership is
reserved in `frontend/`; the React application is not initialized yet.

## Contents

- [Architecture](#architecture)
- [Who owns what](#who-owns-what)
- [Quickstart](#quickstart)
- [Verify it's working](#verify-its-working)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Known gaps / next steps](#known-gaps--next-steps)

## Architecture

Read the target ownership and data flow in [docs/architecture.md](docs/architecture.md).
The ERD is available at [docs/market-screener-erd.drawio](docs/market-screener-erd.drawio).

The intended request flow is:

```text
React frontend -> FastAPI backend -> PostgreSQL / Redis
                                      -> Python worker
                                      -> C++ backtest engine
```

The public API contracts are documented in [docs/api-contracts.md](docs/api-contracts.md).
Frontend request/response examples are in [docs/api-json-guide.md](docs/api-json-guide.md).
The backtest wrapper, data validation, and database mapping are in
[docs/data-contract.md](docs/data-contract.md).

## Project layout

| Directory   | Responsibility                                          |
| ----------- | ------------------------------------------------------- |
| `frontend/` | Browser application; calls FastAPI only                 |
| `backend/`  | FastAPI public API, auth, and persistence orchestration |
| `quant/`    | Stateless market and portfolio calculations             |
| `worker/`   | Ingestion, scoring, and job orchestration               |
| `backtest/` | C++ accounting engine and its input/output contract     |
| `screener/` | Existing research and screening pipeline                |

Frontend setup notes are in [frontend/README.md](frontend/README.md).

## Who owns what

Agree on the contract below _before_ building in parallel — it's the seam between your two pieces of work.

|               | FastAPI backend (`/backend`)                                    | Quant and workers (`/quant`, `/worker`, `/screener`)                     |
| ------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Owns          | Public HTTP API, auth, authorization, persistence orchestration | Ingestion, features, scoring, portfolio math, and backtest orchestration |
| Doesn't touch | Quant formulas or provider-specific data cleaning               | Browser auth and public HTTP concerns                                    |
| Contract      | Returns stable JSON documented in `docs/api-contracts.md`       | Produces validated data and job results                                  |

If one of you changes a JSON field, update the contract document and the Pydantic schema together.

## Target quickstart

Prerequisites: Docker Desktop or Colima, Python 3.12+, and Git.

Set local development variables in your shell or an ignored `.env` file:

```bash
export POSTGRES_USER=portfolio_user
export POSTGRES_PASSWORD=replace-with-a-local-password
export POSTGRES_DB=portfolio
export JWT_SECRET=replace-with-a-long-random-local-secret
```

Start the target FastAPI backend and its dependencies:

```bash
docker compose up --build backend quant postgres redis
```

Check the target API:

```bash
curl http://localhost:8081/healthz
```

Run price ingestion locally when you need a fresh snapshot:

```bash
/opt/homebrew/bin/python3.12 -m venv screener/.venv
screener/.venv/bin/python -m pip install -r screener/requirements.txt
screener/.venv/bin/python screener/ingest_prices.py --period 2y
```

After setup, the common checks are also available through `make`:

```bash
make validate
make compose-config
make ingest-prices
```

The generated `data/price_bars.csv` is local and ignored by Git. The next
worker step loads this normalized shape into PostgreSQL `price_bars`.

Prerequisites: Python 3.12+, Docker Desktop or Colima, and Git.

### Run the quant service locally

```bash
cd quant
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Checkpoint:** `curl localhost:8000/healthz` returns `{"status":"ok"}`.

You can also hit yfinance-backed market data directly:

```bash
curl "localhost:8000/market/quotes?tickers=SPY,QQQ,AAPL,MSFT"
curl "localhost:8000/market/history?tickers=SPY,AAPL&period=1mo&interval=1d"
```

### Run the full local stack

```bash
docker compose up --build backend quant postgres redis
```

Check the services:

```bash
curl http://localhost:8081/healthz
curl http://localhost:8000/healthz
```

On Apple Silicon with Colima, this compose file defaults services to `linux/arm64`.
Override with `DOCKER_PLATFORM=linux/amd64` only if your Docker VM is amd64.

The Compose Postgres host port is `15432` to avoid conflicts with a local Postgres on `5432`:

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\dt'
```

## Verify it's working

This verifies the FastAPI public backend.

```bash
# 1. Register — copy the "token" from the response
curl -X POST localhost:8081/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@drexel.edu","password":"testpass123"}'

# 2. Create a portfolio — copy the "id" from the response
curl -X POST localhost:8081/api/v1/portfolios \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name":"tech","holdings":[{"ticker":"AAPL","weight":0.5},{"ticker":"MSFT","weight":0.5}]}'

# 3. Get analytics — this is the FastAPI-to-quant round trip
curl localhost:8081/api/v1/portfolios/<ID>/analytics \
  -H "Authorization: Bearer <TOKEN>"
```

**Success looks like:** a JSON body with `annualized_return`, `volatility`, `sharpe_ratio`, `max_drawdown` — real numbers, not zeros or nulls.

For a direct quant-service market data check:

```bash
curl "localhost:8000/market/quotes"
curl "localhost:8000/market/history?tickers=SPY&period=5d&interval=1d"
```

## Troubleshooting

| Symptom                                      | Likely cause                                                                   | Fix                                                                                                          |
| -------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| FastAPI returns 502 on `/analytics`          | Python quant service isn't running, or `QUANT_SERVICE_URL` is wrong            | Check `curl localhost:8000/healthz` works first                                                              |
| Python returns 422 on `/analytics/portfolio` | Weights don't sum to 1.0, or a ticker is invalid/delisted                      | Check the error detail in the response body                                                                  |
| `psql: command not found`                    | No Postgres client installed locally                                           | See the brew/apt note in step 2, or use `docker exec -it pg-portfolio psql -U postgres -d portfolio` instead |
| Postgres connection refused                  | Container isn't up yet or port 5432 is already taken by another local Postgres | `docker ps` to check it's running; if port's taken, stop the other Postgres or remap the port                |
| `401 Unauthorized` on portfolio routes       | Missing or expired `Authorization: Bearer <token>` header                      | Re-run login/register to get a fresh token                                                                   |
| Docker Compose `backend` exits immediately   | It started before Postgres was ready                                           | Check `docker compose logs backend`                                                                          |

## Project layout

```
backend/                Target public FastAPI backend
  app/
    routers/             auth, portfolios, analytics
    auth.py              JWT and password hashing
    db.py                PostgreSQL pool/lifespan
    schemas.py           request/response models

quant/                  Stateless Python quant service (FastAPI)
  app/
    routers/             market and portfolio analytics routes
    services/            market data and quant calculations
    models/              Pydantic schemas

screener/               Existing sector/industry/company momentum pipeline
  ingest_prices.py       verified Yahoo Finance price ingestion
  pipeline/              reusable data and scoring calculations

worker/                 Background job ownership and future orchestration
backtest/               C++ backtest engine and build/tests
docs/                   Architecture, API JSON contracts, and ERD
docker-compose.yml      local Postgres, Redis, quant, and FastAPI
```

## Known gaps / next steps

In order of what to tackle next:

1. **Add PostgreSQL migrations** for securities, price bars, fundamentals, scores, and backtests.
2. **Move ingestion orchestration into `worker/`** while reusing `screener/pipeline` calculations.
3. **Add the ranking API** and persist score components.
4. **Expose the C++ backtest engine** through `worker/run_backtest.py`.
5. **Wire Redis job state and WebSocket progress.**
6. **Build the React dashboard and deploy the demo.**
