"""Price-based metrics shared by every level of the funnel.

The same math scores sector ETFs, industry indices, and individual
stocks, so a "strong sector" and a "strong stock" mean the same thing.
"""

import pandas as pd

ONE_MONTH = 21
THREE_MONTHS = 63
SIX_MONTHS = 126
TRADING_DAYS_PER_YEAR = 252


def trailing_return(close: pd.Series, days: int) -> float:
    close = close.dropna()
    if len(close) <= days:
        return float("nan")
    return close.iloc[-1] / close.iloc[-days - 1] - 1


def annualized_volatility(close: pd.Series, days: int) -> float:
    returns = close.dropna().pct_change().dropna().tail(days)
    if returns.empty:
        return float("nan")
    return returns.std() * (TRADING_DAYS_PER_YEAR ** 0.5)


def max_drawdown(close: pd.Series, days: int) -> float:
    recent = close.dropna().tail(days)
    if recent.empty:
        return float("nan")
    running_max = recent.cummax()
    return (recent / running_max - 1).min()


def momentum_metrics(close: pd.Series, benchmark_close: pd.Series) -> dict[str, float]:
    """The metric row every scan level computes for one price series."""
    return_3m = trailing_return(close, THREE_MONTHS)
    benchmark_3m = trailing_return(benchmark_close, THREE_MONTHS)
    return {
        "return_1m": trailing_return(close, ONE_MONTH),
        "return_3m": return_3m,
        "return_6m": trailing_return(close, SIX_MONTHS),
        "rs_3m": return_3m - benchmark_3m,
        "volatility_3m": annualized_volatility(close, THREE_MONTHS),
        "drawdown_3m": max_drawdown(close, THREE_MONTHS),
    }


def composite_score(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted blend of cross-sectional percentile ranks, scaled 0-100.

    `low_volatility` ranks -volatility_3m so that calmer names score higher;
    every other weight key names a column ranked higher-is-better.
    """
    score = pd.Series(0.0, index=frame.index)
    for metric, weight in weights.items():
        if metric == "low_volatility":
            ranked = (-frame["volatility_3m"]).rank(pct=True)
        else:
            ranked = frame[metric].rank(pct=True)
        score += weight * ranked.fillna(0.5)
    return score * 100
