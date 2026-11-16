"""Tail-risk and benchmark-sensitivity measures for a finished backtest.

Pure functions over the engine's daily series, computed once at import time
and stored, rather than recomputed per request: the inputs never change
after a run completes.

Conventions, chosen to match the rest of the stack:

    simple returns          the engine's equity curve compounds them, and
                            they aggregate linearly across holdings
    VaR/CVaR sign           returned negative, as a loss (-0.023 = "a 2.3%
                            loss"), so they sit on the same scale as
                            max_drawdown instead of flipping sign
    historical, not normal  a parametric VaR assumes returns are Gaussian,
                            which is exactly wrong in the tail this is
                            meant to measure
"""

from __future__ import annotations

import math


def _clean(values: list[float]) -> list[float]:
    return [v for v in values if v is not None and math.isfinite(v)]


def historical_var(returns: list[float], confidence: float = 0.95) -> float | None:
    """The loss the worst (1 - confidence) of days exceeded.

    Empirical quantile, no distributional assumption. Returned negative.
    """
    cleaned = sorted(_clean(returns))
    if len(cleaned) < 2:
        return None
    # Lower tail: at 95% confidence this is the 5th percentile.
    index = int(math.floor((1.0 - confidence) * len(cleaned)))
    index = min(max(index, 0), len(cleaned) - 1)
    return float(cleaned[index])


def conditional_var(returns: list[float], confidence: float = 0.95) -> float | None:
    """Expected shortfall: the average loss on days worse than the VaR.

    VaR says how bad a bad day is; CVaR says how bad it gets once you are
    already having one, which is the number that matters when the tail is
    fat.
    """
    cleaned = sorted(_clean(returns))
    if len(cleaned) < 2:
        return None
    cutoff = int(math.floor((1.0 - confidence) * len(cleaned)))
    tail = cleaned[: max(cutoff, 1)]
    return float(sum(tail) / len(tail))


def beta(portfolio_returns: list[float], benchmark_returns: list[float]) -> float | None:
    """Sensitivity to the benchmark: cov(p, b) / var(b).

    1.0 moves with the market, below 1.0 is defensive, above amplifies.
    Undefined when the benchmark never moves.
    """
    pairs = [
        (p, b)
        for p, b in zip(portfolio_returns, benchmark_returns)
        if p is not None and b is not None and math.isfinite(p) and math.isfinite(b)
    ]
    if len(pairs) < 2:
        return None

    n = len(pairs)
    mean_p = sum(p for p, _ in pairs) / n
    mean_b = sum(b for _, b in pairs) / n
    covariance = sum((p - mean_p) * (b - mean_b) for p, b in pairs) / (n - 1)
    variance_b = sum((b - mean_b) ** 2 for _, b in pairs) / (n - 1)
    if variance_b == 0:
        return None
    return float(covariance / variance_b)


def returns_from_values(values: list[float]) -> list[float]:
    """Simple period returns from a value series."""
    out = []
    for previous, current in zip(values, values[1:]):
        if previous in (None, 0) or current is None:
            continue
        out.append(current / previous - 1.0)
    return out
