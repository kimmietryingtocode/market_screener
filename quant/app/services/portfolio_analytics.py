"""
Core quant calculations: pulls historical prices, computes portfolio-level
return/risk metrics. Kept as pure functions operating on pandas/numpy so
they're independently unit-testable without spinning up FastAPI or hitting
the network — only `fetch_price_history` touches yfinance.

Metric definitions here must match the C++ backtest engine's
(backtest/src/backtest.cpp, documented in its statistics_notes.csv output),
because the two are shown side by side to users:

    total/annualized return  compounded from SIMPLE daily returns
    volatility               sample stddev of simple returns x sqrt(252)
    sharpe                   (mean - rf) / stddev x sqrt(252), rf = 0 to match
    max drawdown             measured from starting capital, not from the
                             first observed return

Simple (not log) returns throughout, for two reasons: a weighted portfolio's
return is the weighted sum of its holdings' SIMPLE returns — log returns do
not aggregate linearly across assets — and drawdown/compounding arithmetic
is only valid on simple returns.
"""

import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS_PER_YEAR = 252
# Zero to match the engine, which reports an excess-return-free Sharpe.
# Both sides must use the same convention or their numbers are not comparable.
RISK_FREE_RATE_ANNUAL = 0.0
# Below this, dispersion-based statistics are noise rather than signal. Two
# observations will happily produce a volatility and a Sharpe; neither means
# anything, and a 0.0 drawdown on a halved portfolio is actively misleading.
MIN_RETURN_OBSERVATIONS = 20


def fetch_price_history(tickers: list[str], lookback: str) -> pd.DataFrame:
    """Adjusted close prices for all tickers, aligned on date index."""
    data = yf.download(tickers, period=lookback, auto_adjust=True, progress=False)
    if data.empty:
        raise ValueError("no price data returned for given tickers/lookback")

    # yfinance returns a MultiIndex column frame for multiple tickers,
    # a flat frame for a single ticker — normalize both to a DataFrame
    # of close prices keyed by ticker.
    if isinstance(data.columns, pd.MultiIndex):
        prices = data["Close"]
    else:
        prices = data[["Close"]]
        prices.columns = tickers

    return prices.dropna(how="all")


def compute_portfolio_returns(prices: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Daily simple returns of the weighted portfolio."""
    simple_returns = prices.pct_change().dropna(how="any")
    ordered_weights = np.array([weights[t] for t in simple_returns.columns])
    return simple_returns.dot(ordered_weights)


def _equity_curve(daily_returns: pd.Series) -> pd.Series:
    """Cumulative value of 1 unit of capital, starting at 1.0.

    The leading 1.0 matters: without it the curve begins *after* the first
    return, so a portfolio that only ever falls shows no peak to fall from
    and reports zero drawdown.
    """
    return pd.concat([pd.Series([1.0]), (1.0 + daily_returns).cumprod()], ignore_index=True)


def total_return(daily_returns: pd.Series) -> float:
    """Compounded return over the whole observed period."""
    if daily_returns.empty:
        return float("nan")
    return float((1.0 + daily_returns).prod() - 1.0)


def annualized_return(daily_returns: pd.Series) -> float:
    """Geometric (compound) annualized return.

    Not mean x 252: that arithmetic shortcut overstates volatile series
    badly and cannot be reconciled with a compounded equity curve.
    """
    if daily_returns.empty:
        return float("nan")
    growth = float((1.0 + daily_returns).prod())
    if growth <= 0:
        return float("nan")  # wiped out; an annual rate is not meaningful
    return growth ** (TRADING_DAYS_PER_YEAR / len(daily_returns)) - 1.0


def annualized_volatility(daily_returns: pd.Series) -> float:
    return float(daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe_ratio(ann_return: float, ann_vol: float, risk_free: float = RISK_FREE_RATE_ANNUAL) -> float:
    if ann_vol == 0 or not np.isfinite(ann_vol):
        return 0.0
    return float((ann_return - risk_free) / ann_vol)


def max_drawdown(daily_returns: pd.Series) -> float:
    """Worst peak-to-trough decline, measured from starting capital."""
    if daily_returns.empty:
        return float("nan")
    curve = _equity_curve(daily_returns)
    drawdown = (curve - curve.cummax()) / curve.cummax()
    return float(drawdown.min())


def _json_safe(value: float) -> float | None:
    """NaN and Infinity are not valid JSON. A metric that could not be
    computed is null, not a number that silently poisons arithmetic
    downstream."""
    return float(value) if np.isfinite(value) else None


def compute_portfolio_analytics(tickers: list[str], weights: dict[str, float], lookback: str) -> dict:
    prices = fetch_price_history(tickers, lookback)
    daily_returns = compute_portfolio_returns(prices, weights)

    if len(daily_returns) < MIN_RETURN_OBSERVATIONS:
        raise ValueError(
            f"insufficient price history: {len(daily_returns)} daily returns for lookback "
            f"'{lookback}', need at least {MIN_RETURN_OBSERVATIONS}"
        )

    ann_ret = annualized_return(daily_returns)
    ann_vol = annualized_volatility(daily_returns)

    return {
        "annualized_return": _json_safe(ann_ret),
        "volatility": _json_safe(ann_vol),
        "sharpe_ratio": _json_safe(sharpe_ratio(ann_ret, ann_vol)),
        "max_drawdown": _json_safe(max_drawdown(daily_returns)),
        "total_return": _json_safe(total_return(daily_returns)),
        "lookback": lookback,
        "observations": len(daily_returns),
    }
