import numpy as np
import pandas as pd
import pytest

from app.services.portfolio_analytics import (
    annualized_return,
    annualized_volatility,
    compute_portfolio_analytics,
    compute_portfolio_returns,
    max_drawdown,
    sharpe_ratio,
)


def _prices(values: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.date_range("2025-01-01", periods=len(next(iter(values.values())))))


def test_compute_portfolio_returns_weights_the_log_returns():
    prices = _prices({"A": [100, 110, 121], "B": [50, 50, 50]})  # A: +10%/+10%, B: flat
    returns = compute_portfolio_returns(prices, {"A": 1.0, "B": 0.0})

    assert len(returns) == 2  # first row dropped (no prior day to diff against)
    assert returns.iloc[0] == pytest.approx(np.log(1.10), abs=1e-9)


def test_compute_portfolio_returns_only_uses_given_weights():
    prices = _prices({"A": [100, 200], "B": [100, 100]})
    returns = compute_portfolio_returns(prices, {"A": 0.0, "B": 1.0})

    assert returns.iloc[0] == pytest.approx(0.0)  # B is flat, A's 100% jump is weighted 0


def test_annualized_return_scales_mean_daily_by_trading_days():
    daily = pd.Series([0.01, 0.01, 0.01])
    assert annualized_return(daily) == pytest.approx(0.01 * 252)


def test_sharpe_ratio_is_zero_when_volatility_is_zero():
    assert sharpe_ratio(ann_return=0.1, ann_vol=0.0) == 0.0


def test_sharpe_ratio_nets_out_the_risk_free_rate():
    # ann_return exactly equal to the hardcoded 4% risk-free rate -> zero excess return
    assert sharpe_ratio(ann_return=0.04, ann_vol=0.2) == pytest.approx(0.0)


def test_max_drawdown_on_a_known_series():
    # Portfolio: 100 -> 120 (peak) -> 90 (trough) -> 100. Drawdown from 120 to 90 is -25%.
    daily = pd.Series([0.20, -0.25, 0.1111111])
    assert max_drawdown(daily) == pytest.approx(-0.25, abs=1e-4)


def test_max_drawdown_is_zero_for_a_monotonically_rising_series():
    daily = pd.Series([0.01, 0.02, 0.03])
    assert max_drawdown(daily) == pytest.approx(0.0)


def test_compute_portfolio_analytics_uses_the_injected_price_fetcher(monkeypatch):
    prices = _prices({"AAPL": [100, 110, 99], "MSFT": [50, 55, 50]})
    monkeypatch.setattr(
        "app.services.portfolio_analytics.fetch_price_history",
        lambda tickers, lookback: prices,
    )

    result = compute_portfolio_analytics(["AAPL", "MSFT"], {"AAPL": 0.5, "MSFT": 0.5}, "3mo")

    assert result["lookback"] == "3mo"
    assert set(result) == {"annualized_return", "volatility", "sharpe_ratio", "max_drawdown", "lookback"}
