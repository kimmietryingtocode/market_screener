# Point-in-time research backtester

The MVP has one strategy path and one accounting engine:

- Python produces dated target weights from Yahoo prices, SEC facts, and explicit manual Summary reviews.
- C++ executes those targets chronologically, charges costs, and records cash, synthetic total-return units, holdings, and performance.
- Historical diagnostics and observed forward runs are labeled separately. Current selections or FactSet forecasts never become historical evidence.

The supported service is the offline research workflow over archived inputs,
plus SEC collection and manual Summary review. The live Yahoo sweep remains a
research-only queue generator; automated forward portfolio publication waits
for an immutable, correction-aware Yahoo observation store.

## Release gate

Run the complete offline gate and corrected September experiment from the repository root:

```bash
screener/venv/bin/python -m backtest verify
```

For a clean checkout without private September inputs, run the synthetic accounting, timing, and end-to-end fixtures:

```bash
screener/venv/bin/python -m backtest verify --tests-only
```

The gate compares the C++ simulator to an independent Python calculator at every session. Currency differences must be below `$0.000001`; weight differences must be below `1e-12`. It covers full investment, partial rebalancing, unchanged targets, liquidation, cash, costs, rising/falling prices, and adjusted-price split/dividend examples. Appending future prices and filings must leave earlier targets and ledgers byte-identical.

Each experiment writes `accuracy_report.md`, `accuracy_report.json`, `coverage_report.csv`, `comparison.csv`, and per-scenario targets, trades, holdings, account ledger, equity curve, statistics, and configuration.

## Workflow

```bash
# 1. Diversified Yahoo research sweep
screener/venv/bin/python -m backtest sweep

# 2. Archive a manually exported FactSet Summary
screener/venv/bin/python -m backtest import-summary \
  --screen-run data/screener/<screen-run>/top10_review \
  --factset-dir /path/to/summary/files

# 3. Edit the generated review_template.csv, then publish the complete review
screener/venv/bin/python -m backtest review-summary \
  --package data/screener/<screen-run>/top10_review/factset/<import-id> \
  --decisions /path/to/review.csv

# 4. Replay observed forward decisions
screener/venv/bin/python -m backtest forward \
  --cache-dir data/backtest/<observed-cache> \
  --as-of 2026-09-30T18:00:00-04:00

# 5. Create a compact report from a completed run
screener/venv/bin/python -m backtest report --run-dir data/backtest/<run>
```

Review decisions are `approve`, `reject`, or `needs_review` and must cover every queue member. Financial-gate overrides require a reason. Structural and identity failures cannot be overridden. Reviews keep the original import's 120-day expiry; reimporting an identical file does not backdate or extend its evidence.

The proposed primary portfolio is capped equal weight with no hard trend gate. Excluded names leave residual cash. `--strategy full` retains the sector/industry hierarchy, inverse volatility, 200-session trend rule, 15% position cap, and 40% sector cap as a control.

Runs report one of `collecting`, `pending`, `blocked`, or `completed`. Missing required signal or held-position prices block the affected run instead of being forward-filled.

## Timing and accounting

- Signals occur at 6 p.m. America/New_York on the final NYSE session of each month.
- Execution occurs at the following NYSE session's close. Early closes and holidays come from the versioned calendar rules, not missing Yahoo rows.
- A position bought at an execution close does not receive that session's preceding return.
- Positions are synthetic total-return units. Adjusted-close returns already contain split and dividend effects, so the simulator never credits them again.
- Entry costs use starting capital as the return denominator. Drawdown includes starting capital.
- CAGR uses actual elapsed calendar time. Daily volatility and Sharpe use 252 sessions, sample standard deviation, and a zero cash rate.
- Turnover is absolute bought-plus-sold notional divided by pre-trade equity at each rebalance, summed through the run.
- Average cash includes every session, including the initial waiting period and later all-cash periods.

Yahoo adjusted history is structurally validated for positive finite values, duplicates, NYSE-session alignment, identity uniqueness, and suspicious discontinuities. Passing those checks is not independent verification of Yahoo's data.

## September diagnostic

```bash
screener/venv/bin/python -m backtest experiment
```

This reruns fixed-basket equal weight, SEC equal weight, SEC plus trend, and the full hierarchy over identical 2023-2025 dates, prices, costs, and execution rules. It is explicitly labeled `historical_diagnostic_current_selection_biased`: the September 2026 shortlist and a price archive first observed in 2026 are applied backward. It is useful for isolating mechanics, not estimating a historically investable full-market strategy.

## C++ simulator

```bash
make -C backtest test
make -C backtest
```

The supported target contract is:

```text
signal_at,execution_date,security_id,target_weight,provenance_id
```

An empty `security_id` row is an explicit all-cash rebalance. No row means no decision, not liquidation. `account_ledger.csv` and `holdings.csv` expose the daily state needed for independent reconciliation.

SEC collection and calculation details are in [fundamentals/README.md](fundamentals/README.md).
