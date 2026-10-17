import numpy as np
import pandas as pd
import pytest

from app.services.portfolio_analytics import (
    MIN_RETURN_OBSERVATIONS,
    annualized_return,
    annualized_volatility,
    compute_portfolio_analytics,
    compute_portfolio_returns,
    max_drawdown,
    sharpe_ratio,
    total_return,
)


def _prices(values: dict[str, list[float]]) -> pd.DataFrame:
    length = len(next(iter(values.values())))
    return pd.DataFrame(values, index=pd.date_range("2025-01-01", periods=length))


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values)


# --- portfolio aggregation ------------------------------------------------

def test_portfolio_return_is_the_weighted_sum_of_simple_returns():
    # A +10%, B flat; a 50/50 book should return +5% that day.
    prices = _prices({"A": [100, 110], "B": [50, 50]})
    returns = compute_portfolio_returns(prices, {"A": 0.5, "B": 0.5})

    assert returns.iloc[0] == pytest.approx(0.05)


def test_single_asset_portfolio_tracks_that_asset_exactly():
    prices = _prices({"A": [100, 110, 121]})
    returns = compute_portfolio_returns(prices, {"A": 1.0})

    assert returns.iloc[0] == pytest.approx(0.10)
    assert returns.iloc[1] == pytest.approx(0.10)


def test_zero_weighted_holding_is_ignored():
    prices = _prices({"A": [100, 200], "B": [100, 100]})
    returns = compute_portfolio_returns(prices, {"A": 0.0, "B": 1.0})

    assert returns.iloc[0] == pytest.approx(0.0)


# --- returns --------------------------------------------------------------

def test_total_return_compounds_rather_than_adds():
    # +10% then +10% is +21%, not +20%.
    assert total_return(_series([0.10, 0.10])) == pytest.approx(0.21)


def test_total_return_matches_the_underlying_price_move():
    prices = _prices({"A": [100, 90, 50]})
    returns = compute_portfolio_returns(prices, {"A": 1.0})

    assert total_return(returns) == pytest.approx(-0.50)


def test_annualized_return_is_geometric_not_mean_times_252():
    daily = _series([0.001] * 252)
    expected = 1.001**252 - 1  # compounding, ~28.7%

    assert annualized_return(daily) == pytest.approx(expected)
    # The old arithmetic shortcut would have said 0.001*252 = 25.2%.
    assert annualized_return(daily) != pytest.approx(0.252, abs=1e-3)


def test_annualized_return_over_exactly_one_year_equals_total_return():
    daily = _series([0.0005] * 252)

    assert annualized_return(daily) == pytest.approx(total_return(daily))


def test_annualized_return_is_none_worthy_when_capital_is_wiped_out():
    # -100% in a day leaves nothing to annualize.
    assert np.isnan(annualized_return(_series([-1.0, 0.05])))


# --- risk -----------------------------------------------------------------

def test_annualized_volatility_scales_by_sqrt_252():
    daily = _series([0.01, -0.01, 0.02, -0.02])
    assert annualized_volatility(daily) == pytest.approx(daily.std() * np.sqrt(252))


def test_sharpe_is_zero_when_volatility_is_zero():
    assert sharpe_ratio(0.1, 0.0) == 0.0


def test_sharpe_defaults_to_a_zero_risk_free_rate_to_match_the_engine():
    """The C++ engine reports mean/stddev*sqrt(252) with no excess-return
    adjustment. If this service netted out a different rate, the two numbers
    shown side by side would never reconcile."""
    assert sharpe_ratio(0.20, 0.10) == pytest.approx(2.0)


def test_sharpe_accepts_an_explicit_risk_free_rate():
    assert sharpe_ratio(0.20, 0.10, risk_free=0.04) == pytest.approx(1.6)


# --- drawdown -------------------------------------------------------------

def test_max_drawdown_measures_from_starting_capital():
    """Regression: a portfolio that only falls used to report 0.0 because
    the curve started after the first return, so there was no prior peak."""
    prices = _prices({"A": [100, 50]})
    returns = compute_portfolio_returns(prices, {"A": 1.0})

    assert max_drawdown(returns) == pytest.approx(-0.50)


def test_max_drawdown_never_exceeds_minus_one():
    """Regression: compounding log returns as if simple produced -138% on a
    75% fall, which is impossible."""
    for final in (50, 30, 25, 10):
        prices = _prices({"A": [100, final]})
        returns = compute_portfolio_returns(prices, {"A": 1.0})
        drawdown = max_drawdown(returns)

        assert -1.0 <= drawdown <= 0.0
        assert drawdown == pytest.approx(final / 100 - 1)


def test_max_drawdown_finds_the_worst_peak_to_trough_not_the_last_value():
    # 100 -> 120 (peak) -> 60 (trough) -> 110: worst decline is 120->60.
    prices = _prices({"A": [100, 120, 60, 110]})
    returns = compute_portfolio_returns(prices, {"A": 1.0})

    assert max_drawdown(returns) == pytest.approx(-0.50)


def test_max_drawdown_is_zero_for_a_monotonically_rising_series():
    assert max_drawdown(_series([0.01, 0.02, 0.03])) == pytest.approx(0.0)


# --- insufficient history -------------------------------------------------

def test_too_little_history_raises_instead_of_returning_misleading_numbers(monkeypatch):
    """Two observations can produce a volatility, a Sharpe and a 0.0
    drawdown. None of them mean anything, and users cannot tell."""
    prices = _prices({"A": [100, 50]})
    monkeypatch.setattr("app.services.portfolio_analytics.fetch_price_history", lambda t, l: prices)

    with pytest.raises(ValueError, match="insufficient price history"):
        compute_portfolio_analytics(["A"], {"A": 1.0}, "5d")


def test_exactly_the_minimum_history_is_accepted(monkeypatch):
    values = [100 + i for i in range(MIN_RETURN_OBSERVATIONS + 1)]
    monkeypatch.setattr(
        "app.services.portfolio_analytics.fetch_price_history",
        lambda t, l: _prices({"A": values}),
    )

    result = compute_portfolio_analytics(["A"], {"A": 1.0}, "1mo")

    assert result["observations"] == MIN_RETURN_OBSERVATIONS


def test_result_never_contains_nan_or_inf(monkeypatch):
    """NaN is not valid JSON; an uncomputable metric must be null."""
    values = [100.0] * (MIN_RETURN_OBSERVATIONS + 1)  # zero volatility
    monkeypatch.setattr(
        "app.services.portfolio_analytics.fetch_price_history",
        lambda t, l: _prices({"A": values}),
    )

    result = compute_portfolio_analytics(["A"], {"A": 1.0}, "1mo")

    for key, value in result.items():
        if isinstance(value, float):
            assert np.isfinite(value), f"{key} is not JSON-safe: {value}"


def test_analytics_reports_the_observation_count_it_used(monkeypatch):
    values = [100 + i for i in range(40)]
    monkeypatch.setattr(
        "app.services.portfolio_analytics.fetch_price_history",
        lambda t, l: _prices({"A": values}),
    )

    result = compute_portfolio_analytics(["A"], {"A": 1.0}, "3mo")

    assert result["observations"] == 39
    assert result["lookback"] == "3mo"
