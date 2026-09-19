"""Independent, test-only portfolio calculator for the C++ accuracy gate."""

from __future__ import annotations

import math

import pandas as pd


def simulate(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    initial_capital: float,
    cost_bps: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cash = initial_capital
    units: dict[str, float] = {}
    ledgers, holdings, trades = [], [], []
    previous = initial_capital
    cost_rate = cost_bps / 10_000
    by_date = {day: group for day, group in targets.groupby("execution_date")}
    for day, row in prices.iterrows():
        pre_trade = cash + sum(quantity * row[ticker] for ticker, quantity in units.items())
        traded = costs = 0.0
        if day in by_date:
            requested = by_date[day]
            weights = {
                record.security_id: record.target_weight
                for record in requested.itertuples()
                if record.security_id
            }
            assert sum(weights.values()) <= 1 + 1e-12
            names = set(units) | set(weights)
            investable = pre_trade
            for _ in range(100):
                notional = sum(
                    abs(investable * weights.get(ticker, 0.0) - units.get(ticker, 0.0) * row[ticker])
                    for ticker in names
                )
                next_value = max(0.0, pre_trade - notional * cost_rate)
                if abs(next_value - investable) <= max(1.0, pre_trade) * 1e-14:
                    investable = next_value
                    break
                investable = next_value
            new_units = {}
            for ticker in names:
                current = units.get(ticker, 0.0)
                target_value = investable * weights.get(ticker, 0.0)
                target_units = target_value / row[ticker]
                notional = abs(target_units - current) * row[ticker]
                if notional > 1e-8:
                    fee = notional * cost_rate
                    trades.append(
                        {
                            "date": day,
                            "ticker": ticker,
                            "notional": notional,
                            "transaction_cost": fee,
                        }
                    )
                    traded += notional
                    costs += fee
                if target_units > 1e-12:
                    new_units[ticker] = target_units
            invested = sum(quantity * row[ticker] for ticker, quantity in new_units.items())
            cash = pre_trade - invested - costs
            if cash < -1e-6:
                raise AssertionError(f"negative cash: {cash}")
            cash = max(0.0, cash)
            units = new_units
        invested = sum(quantity * row[ticker] for ticker, quantity in units.items())
        value = cash + invested
        ledgers.append(
            {
                "date": day,
                "pre_trade_value": pre_trade,
                "cash": cash,
                "invested_value": invested,
                "traded_notional": traded,
                "transaction_cost": costs,
                "portfolio_value": value,
                "daily_return": value / previous - 1,
            }
        )
        for ticker, quantity in units.items():
            market_value = quantity * row[ticker]
            holdings.append(
                {
                    "date": day,
                    "ticker": ticker,
                    "units": quantity,
                    "price": row[ticker],
                    "market_value": market_value,
                    "weight": market_value / value,
                }
            )
        previous = value
    return pd.DataFrame(ledgers), pd.DataFrame(holdings), pd.DataFrame(trades)


def statistics(ledger: pd.DataFrame, trades: pd.DataFrame, initial_capital: float) -> dict[str, float]:
    values = ledger["portfolio_value"]
    returns = ledger["daily_return"]
    years = (ledger["date"].iloc[-1] - ledger["date"].iloc[0]).days / 365.25
    volatility = returns.std(ddof=1) * math.sqrt(252)
    peak = pd.concat([pd.Series([initial_capital]), values], ignore_index=True).cummax().iloc[1:]
    drawdown = values.reset_index(drop=True).div(peak).sub(1)
    trade_by_date = trades.groupby("date")["notional"].sum() if not trades.empty else pd.Series(dtype=float)
    pre_trade = ledger.set_index("date")["pre_trade_value"]
    turnover = sum(notional / pre_trade.loc[day] for day, notional in trade_by_date.items())
    return {
        "ending_value": values.iloc[-1],
        "total_return": values.iloc[-1] / initial_capital - 1,
        "cagr": (values.iloc[-1] / initial_capital) ** (1 / years) - 1,
        "annualized_volatility": volatility,
        "sharpe_ratio": returns.mean() / returns.std(ddof=1) * math.sqrt(252),
        "maximum_drawdown": drawdown.min(),
        "total_turnover": turnover,
        "transaction_costs": trades["transaction_cost"].sum(),
        "average_cash_weight": (ledger["cash"] / values).mean(),
    }
