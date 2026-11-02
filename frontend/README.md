# Signalist Frontend

This directory owns the browser application. It should call the FastAPI backend
only; it must not connect directly to PostgreSQL, Redis, the quant service, or
the backtest engine.

## Current UI

The frontend is a buildless browser app with Google-only account access,
guided portfolio creation, published stock rankings, and asynchronous
backtests. Signed-in screens render backend responses only; unavailable or
unpublished data uses explicit empty and error states.

Run it locally:

```bash
cd frontend
npm start
```

Then open `http://localhost:4173`. The local server adds the Referrer Policy
and Cross-Origin-Opener-Policy headers required for a reliable Google popup
flow.

For Google sign-in, follow `../docs/google-auth-setup.md`. The frontend loads
the public client ID from FastAPI, so it only needs to be pasted once as
`GOOGLE_CLIENT_ID` in the root `.env`. This login flow does not use the OAuth
client secret.

Run the dependency-free browser contract tests with:

```bash
cd frontend
npm test
```

The first-run guide is stored per Google account in browser local storage and
can always be reopened with **Help**. The signed-in flow begins with the latest
real recommendation queue, optionally refines it with a complete FactSet
Standardized Summary CSV package, then lets the user build a portfolio from
those companies or their own tickers. Backtests unlock after a portfolio is
saved. No ranking state substitutes sample results.

The public request and response contract is documented in:

- `../docs/api-contracts.md`
- `../docs/api-json-guide.md`

The frontend should treat the backend as the source of API truth. When a JSON
field changes, update the backend Pydantic schema and both API documents before
updating frontend types.

Backtest creation, history, status polling, validation notes, six result
metrics, and the portfolio-versus-benchmark SVG curve use the existing FastAPI
contracts. Queued runs require the separate backtest worker; the UI never
invents progress while it waits.
