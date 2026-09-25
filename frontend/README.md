# Market Screener Frontend

This directory owns the browser application. It should call the FastAPI backend
only; it must not connect directly to PostgreSQL, Redis, the quant service, or
the backtest engine.

## Planned structure

```text
frontend/
├── src/
│   ├── api/          # FastAPI client functions
│   ├── components/   # Reusable UI components
│   ├── pages/        # Route-level views
│   ├── types/        # API and UI types
│   └── app/           # Application shell and routing
├── public/
└── package.json
```

The public request and response contract is documented in:

- `../docs/api-contracts.md`
- `../docs/api-json-guide.md`

The frontend should treat the backend as the source of API truth. When a JSON
field changes, update the backend Pydantic schema and both API documents before
updating frontend types.

## Current status

The frontend application has not been initialized yet. This directory is the
ownership boundary for the upcoming React application.
