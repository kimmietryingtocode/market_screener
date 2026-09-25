# FastAPI Backend

The backend owns the public HTTP API, authentication, and persistence
orchestration. It does not own provider-specific ingestion or portfolio
calculation formulas.

## Layout

```text
backend/app/
├── core/       # Configuration and authentication/security helpers
├── database/   # PostgreSQL connection pool and lifecycle
├── routers/    # FastAPI HTTP endpoints (controller layer)
├── schemas/    # Pydantic request and response contracts
└── main.py     # FastAPI application assembly
```

The worker and backtest engine are separate from this package. The browser
calls these endpoints through FastAPI and never connects directly to
PostgreSQL, Redis, quant, or C++ backtest processes.

Public JSON contracts are documented in `../docs/api-contracts.md` and
`../docs/api-json-guide.md`.
