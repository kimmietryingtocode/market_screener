"""
Core quant calculations: pulls historical prices, computes portfolio-level
return/risk metrics. Kept as pure functions operating on pandas/numpy so
they're independently unit-testable without spinning up FastAPI or hitting
the network — only `fetch_price_history` touches yfinance.
"""

import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE_ANNUAL = 0.04  # rough proxy; could be parameterized later from FRED


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
    """Daily log returns of the weighted portfolio."""
    log_returns = np.log(prices / prices.shift(1)).dropna()
    ordered_weights = np.array([weights[t] for t in log_returns.columns])
    return log_returns.dot(ordered_weights)


def annualized_return(daily_returns: pd.Series) -> float:
    mean_daily = daily_returns.mean()
    return float(mean_daily * TRADING_DAYS_PER_YEAR)


def annualized_volatility(daily_returns: pd.Series) -> float:
    return float(daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe_ratio(ann_return: float, ann_vol: float) -> float:
    if ann_vol == 0:
        return 0.0
    return float((ann_return - RISK_FREE_RATE_ANNUAL) / ann_vol)


def max_drawdown(daily_returns: pd.Series) -> float:
    cumulative = (1 + daily_returns).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    return float(drawdown.min())


def compute_portfolio_analytics(tickers: list[str], weights: dict[str, float], lookback: str) -> dict:
    prices = fetch_price_history(tickers, lookback)
    daily_returns = compute_portfolio_returns(prices, weights)

    ann_ret = annualized_return(daily_returns)
    ann_vol = annualized_volatility(daily_returns)

    return {
        "annualized_return": ann_ret,
        "volatility": ann_vol,
        "sharpe_ratio": sharpe_ratio(ann_ret, ann_vol),
        "max_drawdown": max_drawdown(daily_returns),
        "lookback": lookback,
    }
