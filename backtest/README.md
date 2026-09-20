# C++ Backtest Engine

The C++ engine is integrated from the `backtest-dev` branch. It is a
deterministic command-line program and does not own authentication,
PostgreSQL, Redis, or HTTP routes.

Build and test it:

```bash
cd backtest
make
make test
```

Run it directly:

```bash
./build/portfolio_backtest \
  --prices prices.csv \
  --universe universe.csv \
  --output-dir output \
  --benchmark SPY
```

The Python worker owns database reads and invokes the engine through
`worker/run_backtest.py`. The engine receives two CSV snapshots and writes
`summary.csv`, `equity_curve.csv`, `trades.csv`, `rebalance_log.csv`, and
`run_config.csv`.

Required correctness rules:

- Do not use fundamentals before their `filing_date`.
- Include transaction costs and slippage in the request.
- Compare portfolio performance with the requested benchmark.
- Produce deterministic output for the same request and input snapshot.
