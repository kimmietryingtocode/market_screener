# Point-in-time SEC fundamentals

This tool reconstructs eligible SEC XBRL facts at explicit decision timestamps. It uses Python 3.11+, pandas, requests, pyarrow, and optional yfinance filing-date prices. There is no database or service dependency.

```bash
export SEC_USER_AGENT='ResearchTool/1.0 your-real-email@example.org'
screener/venv/bin/python -m backtest.fundamentals AAPL MSFT KO \
  --sources edgar \
  --as-of 2024-01-31T18:00:00-05:00 \
  --as-of 2024-02-29T18:00:00-05:00 \
  --output data/fundamentals/example
```

For the compact backtester gate, provide a reviewed ticker-to-CIK map:

```bash
screener/venv/bin/python -m backtest.fundamentals \
  --sources edgar --pit-only --offline \
  --cik-map data/fundamentals/cik_map.csv \
  --as-of 2024-01-31T18:00:00-05:00 \
  --output data/fundamentals/pit
```

## Availability contract

- Every `as_of` is timezone-aware. A model row is built independently for that cutoff.
- SEC acceptance timestamps come from current and historical submissions pages. Date-only evidence is delayed until the following New York calendar day. Unknown availability is excluded.
- Eligibility is applied before tag fallback, revision selection, fiscal-period assignment, Q4 derivation, TTM aggregation, or ratios.
- The latest eligible financial period is selected first. The configured revision policy then selects within that period.
- A derived metric is available no earlier than its latest input. Q4 waits for the annual filing; TTM waits for every component.
- `period_end`, `published_at`, `available_at`, filing date, accession, and per-concept provenance remain distinct.
- Raw response cache versions are immutable. `--refresh` appends observations; `--offline` only replays cache versions known by the cutoff.

EDGAR reconstruction shows what filings disclosed, not when an unknown historical client first received a continuously updated API response. See the [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

## Accounting rules

`config.py` contains the ordered US-GAAP fallback tags, units, instant/duration behavior, and component formulas. Revenue includes `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `RevenueFromContractWithCustomerIncludingAssessedTax`, and `SalesRevenueNet`. Every field records the tag and accession actually used.

- Duration facts and instant balances are resolved separately.
- Standalone quarters are preferred. YTD flows are differenced only with compatible eligible inputs. Additive Q4 values are FY minus Q1-Q3; FY and Q4 remain separate rows.
- EPS and weighted-average shares are never subtracted to manufacture Q4.
- Capex is a positive outflow. FCF is operating cash flow minus capex.
- EBITDA is operating income plus depreciation and amortization. Missing components remain missing, never zero.
- Debt uses explicit current, short-term borrowing, and noncurrent components when a direct total is absent.
- Annual flow ratios use annual flows and average opening/closing balances. Quarterly ROE, ROA, and asset turnover use eligible TTM flows and matching average balances.
- Missing values and zero denominators produce NaN.

The compact PIT export retains one row for every requested cutoff, including `as_of`, period identity, revision/accession, EBITDA/FCF period and TTM values, component fields, and per-metric provenance. It classifies each selected row as `positive`, `nonpositive`, `incomplete`, `missing`, `unsupported`, or `unavailable`; missing insurer EBITDA is not called negative.

## Outputs

- `fundamentals.parquet`: eligible model rows or compact cutoff vintages.
- `superseded.parquet`: eligible SEC alternatives displaced by tag/revision policy.
- `archive.parquet`: recognized SEC versions for audit, including versions not eligible at a given cutoff.
- `snapshots.parquet`: optional price snapshots and observation timestamps.
- `comparison.parquet`: reserved output for explicit Summary reconciliation.

Automatic SimFin, FMP, Alpha Vantage, Finnhub, and Yahoo fundamental adapters were retired. They cannot prove old value vintages on their free tiers. Manual FactSet Summary actuals can be compared explicitly with `compare_frames(edgar, summary, as_of=..., tolerance=0.01)` when identity, duration, currency, unit, and basis match. Values are never averaged or silently coalesced. Disagreements may reflect non-GAAP adjustments, fiscal/calendar alignment, restatement vintage, or period-end versus weighted-average shares.

Optional `--prices yfinance` is isolated from EDGAR. It targets the filing-date close, uses the preceding close on a non-trading date, and excludes a same-day close before that close is observable. Historical valuation remains missing when share or split basis cannot be verified.

## Verification

```bash
PYTHONPATH=.:screener screener/venv/bin/python -m pytest -q backtest/fundamentals/tests
```

Saved SEC JSON and mocked caches cover tag fallback, revisions, after-close filings, 52/53-week calendars, Q4/YTD calculations, missing components, rate limiting, immutable cache versions, Parquet output, and future-data invariance. Model training remains outside this tool; downstream work must use chronological splits and training-only preprocessing.
