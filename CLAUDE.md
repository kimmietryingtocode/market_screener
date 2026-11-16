# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A stock screener and portfolio backtesting platform. Stocks are ranked by momentum and fundamental signals, each score is explained, and portfolio strategies are backtested against a benchmark. Optional LLM reports explain stored facts. They never compute the financial numbers.

The services, all wired together in `docker-compose.yml`:

```text
frontend/ (buildless JS, :4173) -> backend/ FastAPI public API (:8081)
                                     -> PostgreSQL 16 + pgvector (source of truth AND job queue; host port 15432)
                                     -> Redis (cache + rate limiting only; fails open if absent)
                                     -> quant/ FastAPI stateless analytics service (:8000, yfinance-backed)
worker/ (separate long-running loops, no HTTP) -> PostgreSQL, and shells out to backtest/ C++ engine
```

## Commands

Python 3.12 everywhere. CI (`.github/workflows/ci.yml`) is the authoritative recipe. Mirror its env vars and `PYTHONPATH`, or imports break.

```bash
# Full local stack (needs .env; `python scripts/generate_env.py` creates one with random secrets)
docker compose up --build
curl localhost:8081/healthz        # /readyz also checks Postgres

# Migrations (plain SQL files in backend/migrations/, tracked in schema_migrations)
cd backend && python migrate.py            # apply pending
cd backend && python migrate.py --status   # nonzero exit if anything pending

# Backend tests (run from backend/)
cd backend && PYTHONPATH=.:.. python -m pytest app test_migrate.py --ignore=app/routers/test_portfolio_backtest_flow.py
cd backend && PYTHONPATH=.:.. python -m pytest app/services/test_backtest_service.py::test_name   # single test

# Quant tests
cd quant && PYTHONPATH=. python -m pytest app

# C++ engine (must be built before worker tests: several call the real binary)
make -C backtest && make -C backtest test

# Worker / backtest / screener tests (run from repo root)
PYTHONPATH=.:backend python -m pytest worker backtest/tests backtest/fundamentals/tests screener/tests backend/app/routers/test_portfolio_backtest_flow.py

# Frontend (no build step, no deps)
cd frontend && npm start    # http://localhost:4173
cd frontend && npm test     # node --test tests/*.test.cjs

# API contract tests (needs backend + quant running)
newman run postman/market_screener.postman_collection.json

# Misc
make validate          # compileall + drawio XML check
make compose-config
```

**DB-backed tests skip silently** when `TEST_DATABASE_URL` is unset or unreachable (see `backend/app/services/conftest.py`, which `worker/test_*.py` follows too). A green run without a database may have tested nothing. Point it at the compose Postgres: `postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:15432/$POSTGRES_DB`, with migrations applied. `backtest/fundamentals/tests` has to be named explicitly because it is not under `backtest/tests`.

## Architecture notes that span files

**Backend layering** (`backend/app/`): `routers/` → `services/` → `repositories/`. Routers stay thin: they open a connection from `request.app.state.db`, call a service function with `(connection, user_id, payload)`, and turn `services.errors.ServiceError` into HTTP responses with `core.http_errors.to_http_exception`. Business logic lives in services so it can be tested against a real DB without HTTP. Raw SQL goes through psycopg 3 (no ORM). Pydantic contracts live in `schemas/`.

**API contract rule:** when you change a JSON field, update the Pydantic schema, `docs/api-contracts.md`, `docs/api-json-guide.md`, and the frontend in the same change. The Postman collection is run in CI.

**Backtests are async jobs in Postgres, not Redis.** `POST /api/v1/backtests` inserts a `queued` row (202). `worker/run_pending_backtests.py` claims rows with `FOR UPDATE SKIP LOCKED` under a lease, so abandoned `running` rows get requeued. It builds a dated target schedule (`worker/build_targets.py`), writes CSVs, runs the C++ binary (`backtest/build/portfolio_backtest`), and persists results and the equity curve (`worker/persistence.py`). The C++ engine never touches the DB or Redis. It only reads and writes files.

**Data pipeline:** `worker/ingest_prices.py` and `worker/ingest_fundamentals.py` populate `price_bars` and `fundamental_snapshots`. `worker/calculate_scores.py` writes `scores` (percentile ranks), which back the rankings API. Workers reuse calculations from `screener/pipeline/` and `screener/ingest_prices.py` instead of duplicating them. Every worker loop catches and logs errors per iteration, and jobs must be idempotent (upserts).

**Look-ahead bias guard:** the yfinance fundamentals have *estimated* filing dates (period_end + 45d), tagged `data_source='yfinance-estimated-filing-date'`. They are only for "what do we know now" rankings. Point-in-time backtest gating must use real filing dates from `backtest/fundamentals/` (EDGAR) and filter on `data_source` explicitly.

**`backtest/`** has two parts: the C++ accounting engine (`src/`, `include/`) and a Python research workflow (`python -m backtest verify|sweep|import-summary|review-summary|forward`). The verify gate checks the C++ output against an independent Python calculator (`backtest/tests/reference_calculator.py`) to within $0.000001 per session. See `backtest/README.md`.

**AI analysis** (`backend/app/ai/`): LangGraph/LangChain workflow with RAG over pgvector. Reports are queued in `ai_reports` and processed by the `analysis-worker` (`python -m app.ai.worker`). Busy-provider errors are retried. The provider is chosen with `AI_PROVIDER` (`gemini` | `ollama`). With Gemini and `AI_GEMINI_DATA_POLICY=public_only`, private backtest reports are rejected *before* the model is called (`providers.py`). Keep that guard. Re-run `worker/index_ai_documents.py` after changing the embedding provider or model so vector spaces don't mix.

**Frontend** (`frontend/`): plain browser JS, no bundler. It calls only the FastAPI backend and uses Google sign-in (client ID served by the backend). It has no DOM test harness. `tests/app-contract.test.cjs` statically checks that every function `app.js` calls and every element ID it looks up actually exists, because a missing one fails silently inside try/catch at runtime.

## Docker / platform

- The backend image contains `backend/` plus `screener/pipeline` and `worker/index_ai_documents.py`, with `PYTHONPATH=/workspace/backend:/workspace`. The worker image builds the C++ engine in a build stage and uses `ENTRYPOINT python`, so each compose service passes its own `-m worker.<module>`.
- Compose defaults to `linux/arm64`. Set `DOCKER_PLATFORM=linux/amd64` on amd64 hosts.
- Deployment target and plan: `deploymentplan.md` (single free-tier ARM VM, Caddy, Compose).

## Docs

`docs/architecture.md` (ownership boundaries: e.g. `quant/` never writes Postgres, `backend/` never holds quant formulas), `docs/data-contract.md` (backtest engine I/O and validation), `docs/self-hosted-rag.md`, `docs/google-auth-setup.md`. Parts of the root `README.md` (its "Project layout" and "Known gaps" sections) are out of date. Trust the code and per-directory READMEs over it.
