"""Raw SQL for `backtests`, `backtest_results`, and `equity_curve_points`.

Job creation only inserts a queued row — it does not talk to the worker.
That orchestration (queue -> worker/run_pending_backtests.py -> engine)
lives outside the API process; see the architecture doc.
"""

import json
from datetime import date, datetime
from typing import Any, NamedTuple


class BacktestRow(NamedTuple):
    id: int
    portfolio_id: int | None
    status: str
    benchmark: str
    start_date: date
    end_date: date
    rebalance_frequency: str
    created_at: datetime
    completed_at: datetime | None


class BacktestDetailRow(NamedTuple):
    backtest: BacktestRow
    research_outcome: str | None
    publishable: bool
    available_at: datetime | None
    validation_messages: list[dict]


class MetricsRow(NamedTuple):
    total_return: float | None
    annualized_return: float | None
    volatility: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None
    alpha_vs_benchmark: float | None


class EquityCurveRow(NamedTuple):
    trading_date: date
    portfolio_value: float
    benchmark_value: float


_COLUMNS = """
    id, portfolio_id, status, benchmark, start_date, end_date,
    rebalance_frequency, created_at, completed_at
"""


def insert_backtest(
    connection: Any,
    *,
    user_id: int,
    portfolio_id: int,
    start_date: date,
    end_date: date,
    benchmark: str,
    rebalance_frequency: str,
    cost_bps: float,
    initial_capital: float,
) -> BacktestRow:
    parameters = json.dumps({"cost_bps": cost_bps, "initial_capital": initial_capital})
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO backtests
                (user_id, portfolio_id, status, start_date, end_date,
                 benchmark, rebalance_frequency, parameters)
            VALUES (%s, %s, 'queued', %s, %s, %s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (user_id, portfolio_id, start_date, end_date, benchmark, rebalance_frequency, parameters),
        )
        return BacktestRow(*cursor.fetchone())


def list_backtests(connection: Any, user_id: int) -> list[BacktestRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM backtests WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return [BacktestRow(*row) for row in cursor.fetchall()]


def get_owned_backtest(connection: Any, backtest_id: int, user_id: int) -> BacktestDetailRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {_COLUMNS}, research_outcome, publishable, available_at, validation_messages
            FROM backtests WHERE id = %s AND user_id = %s
            """,
            (backtest_id, user_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return BacktestDetailRow(
            backtest=BacktestRow(*row[:9]),
            research_outcome=row[9],
            publishable=bool(row[10]),
            available_at=row[11],
            validation_messages=row[12] or [],
        )


def get_results(connection: Any, backtest_id: int) -> MetricsRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT total_return, annualized_return, volatility,
                   sharpe_ratio, max_drawdown, alpha_vs_benchmark
            FROM backtest_results WHERE backtest_id = %s
            """,
            (backtest_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        # psycopg returns NUMERIC columns as Decimal; cast here so the row
        # actually holds what MetricsRow's type hints promise (float | None)
        # instead of leaking Decimal upward for callers to trip over.
        return MetricsRow(*(float(value) if value is not None else None for value in row))


def get_equity_curve(connection: Any, backtest_id: int) -> list[EquityCurveRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT trading_date, portfolio_value, benchmark_value
            FROM equity_curve_points WHERE backtest_id = %s ORDER BY trading_date
            """,
            (backtest_id,),
        )
        return [
            EquityCurveRow(trading_date=row[0], portfolio_value=float(row[1]), benchmark_value=float(row[2]))
            for row in cursor.fetchall()
        ]
