# Portfolio analysis platform

A small full-stack system for analyzing investment portfolios: create a portfolio, add holdings, get back real risk/return metrics (Sharpe ratio, volatility, max drawdown) computed from actual market data.

Two services talk to each other: a Go API that owns users and data, and a Python service that does the math.

**Status:** backend scaffolded, untested end-to-end. No frontend yet. See [Known gaps](#known-gaps--next-steps).

## Contents
- [Architecture](#architecture)
- [Who owns what](#who-owns-what)
- [Quickstart](#quickstart)
- [Verify it's working](#verify-its-working)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Known gaps / next steps](#known-gaps--next-steps)

## Architecture

![Portfolio platform architecture](docs/architecture.svg)

A request to "show me my portfolio's Sharpe ratio" flows like this: the browser hits the Go API, Go reads the portfolio's holdings from Postgres and forwards them to the Python service, Python pulls real price history and computes the numbers, Go stores and returns the result.

Two rules that keep this simple:
- **Postgres has one writer.** Only the Go API touches it. Python never sees a connection string.
- **Python is stateless.** Every request carries everything it needs (tickers + weights). No portfolio ever "lives" in Python.

Redis isn't pictured because it isn't wired in yet — see [Known gaps](#known-gaps--next-steps) for where it goes.

## Who owns what

Agree on the contract below *before* building in parallel — it's the seam between your two pieces of work.

| | Go API (`/api`) | Python quant service (`/quant`) |
|---|---|---|
| Owns | Users, auth, portfolios, holdings, all Postgres access | Computing return/volatility/Sharpe/max drawdown from price data |
| Doesn't touch | Any finance math | Postgres, auth, anything stateful |
| Contract | Sends `{holdings: [{ticker, weight}], lookback}` to Python | Receives that, returns `{annualized_return, volatility, sharpe_ratio, max_drawdown}` |

The shapes on both sides already match (`portfolio_handler.go`'s `quantAnalyticsRequest` ↔ `schemas.py`'s `AnalyticsRequest`). If one of you needs to change the contract, tell the other person before pushing — a silent field rename breaks the integration with no compile error on either side, since they're two separate languages.

## Quickstart

Each step has a checkpoint. If the checkpoint doesn't match, stop and fix it before moving on — don't chain failures.

**Prereqs:** Go 1.22+, Python 3.12+, Docker Desktop or Colima running, `git`.

### 1. Clone and branch
```bash
git clone <repo-url>
cd portfolio-platform
git checkout -b <yourname>/setup
```

### 2. Start Postgres
```bash
docker run --name pg-portfolio -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=portfolio -p 5432:5432 -d postgres:16-alpine

psql postgres://postgres:postgres@localhost:5432/portfolio -f api/migrations/001_init.sql
```
> No `psql`? `brew install libpq && brew link --force libpq` (Mac) or `apt install postgresql-client` (Linux/WSL).

**Checkpoint:** `psql postgres://postgres:postgres@localhost:5432/portfolio -c '\dt'` lists `users`, `portfolios`, `holdings`, `analytics_runs`.

### 3. Run the Go API
```bash
cd api
go mod tidy
go run ./cmd/server
```
**Checkpoint:** terminal prints `listening on :8080`, and in another terminal `curl localhost:8080/healthz` returns `{"status":"ok"}`.

### 4. Run the Python quant service (separate terminal)
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

### 5. Run both together with Docker Compose (optional, once each runs standalone)
```bash
docker compose up --build
```
Same checkpoints as above, against `localhost:8080` and `localhost:8000`.

On Apple Silicon with Colima, this compose file defaults services to `linux/arm64`.
Override with `DOCKER_PLATFORM=linux/amd64` only if your Docker VM is amd64.

The Compose Postgres host port is `15432` to avoid conflicts with a local Homebrew Postgres on `5432`:
```bash
psql postgres://postgres:postgres@127.0.0.1:15432/portfolio -c '\dt'
```

## Verify it's working

This is the full loop — register, create a portfolio, get real analytics back through both services.

```bash
# 1. Register — copy the "token" from the response
curl -X POST localhost:8080/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@drexel.edu","password":"testpass123"}'

# 2. Create a portfolio — copy the "id" from the response
curl -X POST localhost:8080/api/v1/portfolios \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name":"tech","holdings":[{"ticker":"AAPL","weight":0.5},{"ticker":"MSFT","weight":0.5}]}'

# 3. Get analytics — this is the Go-calls-Python round trip
curl localhost:8080/api/v1/portfolios/<ID>/analytics \
  -H "Authorization: Bearer <TOKEN>"
```
**Success looks like:** a JSON body with `annualized_return`, `volatility`, `sharpe_ratio`, `max_drawdown` — real numbers, not zeros or nulls.

For a direct quant-service market data check:
```bash
curl "localhost:8000/market/quotes"
curl "localhost:8000/market/history?tickers=SPY&period=5d&interval=1d"
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `go run` fails with missing module errors | `go mod tidy` didn't run, or no network access | Re-run `go mod tidy` from `/api` with internet on |
| Go API returns 502 on `/analytics` | Python service isn't running, or `QUANT_SERVICE_URL` is wrong | Check `curl localhost:8000/healthz` works first |
| Python returns 422 on `/analytics/portfolio` | Weights don't sum to 1.0, or a ticker is invalid/delisted | Check the error detail in the response body |
| `psql: command not found` | No Postgres client installed locally | See the brew/apt note in step 2, or use `docker exec -it pg-portfolio psql -U postgres -d portfolio` instead |
| Postgres connection refused | Container isn't up yet or port 5432 is already taken by another local Postgres | `docker ps` to check it's running; if port's taken, stop the other Postgres or remap the port |
| `401 Unauthorized` on portfolio routes | Missing or expired `Authorization: Bearer <token>` header | Re-run login/register to get a fresh token |
| Docker Compose `api` container exits immediately | It started before Postgres was ready | Compose already has a healthcheck dependency — if this still happens, check `docker compose logs api` |

## Project layout

```
api/                    Go service (Gin, pgx, JWT)
  cmd/server/           entrypoint
  internal/
    auth/               JWT + bcrypt
    config/             env var loading
    db/                 Postgres pool
    handlers/           HTTP handlers (auth, portfolios, analytics call-out)
    middleware/          auth middleware
    models/             shared structs
  migrations/           SQL schema

quant/                  Python service (FastAPI)
  app/
    routers/            HTTP routes
    services/           the actual quant math (pure functions, unit-testable)
    models/             pydantic request/response schemas

docker-compose.yml      wires postgres + redis + quant + api together
backtest/               Point-in-time workflow plus C++ target-schedule simulator
screener/               Yahoo sweep and manual FactSet Summary importer
```

The research workflow distinguishes historical diagnostics from observed
forward runs. SEC facts are selected by filing availability, current FactSet
Summary exports never backfill earlier decisions, and archived runs replay from
copied, hashed inputs. See [`backtest/README.md`](backtest/README.md) for the
supported release scope and accuracy gates.

## Known gaps / next steps

In order of what to tackle next:

1. **Confirm the Go service actually builds.** Written carefully but not compiled in the environment it was scaffolded in — first thing to do locally is `go build ./...` and fix whatever surfaces.
2. **Wire Redis into the analytics endpoint** as a cache (key: `portfolio:<id>:analytics`, TTL ~15 min) — deferred on purpose until there's a slow endpoint worth caching, and now there is.
3. **Frontend dashboard** (React) — login, create portfolio, view analytics.
4. **Replace the hardcoded 4% risk-free rate** in `portfolio_analytics.py` with a real lookup (FRED's 3-month T-bill series) — makes the Sharpe ratio defensible if someone asks what rate you used.
5. **Deploy to AWS** — ECS Fargate for both containers + RDS Postgres + ElastiCache Redis is the standard pattern here.
