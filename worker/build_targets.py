"""Expand a backtest's frozen holdings into a dated target schedule.

The API copies a portfolio's ticker/weight rows when the user requests a run,
so later portfolio edits cannot change queued work. The C++ engine needs a
dated schedule instead: an initial purchase plus the same weights at every
month-end rebalance.

Only monthly rebalancing is supported for now; that's the only frequency
month_end_signals implements.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
from zoneinfo import ZoneInfo

from backtest.calendar import month_end_signals, nyse_sessions

SIGNAL_TIME = time(18, 0)
SIGNAL_TIMEZONE = ZoneInfo("America/New_York")
PROVENANCE_ID = "saved-backtest-holdings"


def generate_targets_from_portfolio(connection: Any, backtest_id: int) -> int:
    """Expand one backtest's holding snapshot. Returns target rows written.

    Idempotent: clears any existing rows for this backtest_id first, so a
    retried job doesn't duplicate or layer stale targets on top of new ones.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT portfolio_id, start_date, end_date, rebalance_frequency FROM backtests WHERE id = %s",
            (backtest_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"no backtest with id {backtest_id}")
        portfolio_id, start_date, end_date, rebalance_frequency = row
        if portfolio_id is None:
            raise ValueError(f"backtest {backtest_id} has no source portfolio to build targets from")
        if rebalance_frequency != "monthly":
            raise ValueError(f"unsupported rebalance_frequency: {rebalance_frequency!r} (only 'monthly' is implemented)")

        cursor.execute(
            "SELECT ticker, weight FROM backtest_holdings WHERE backtest_id = %s ORDER BY ticker",
            (backtest_id,),
        )
        holdings = cursor.fetchall()
        if not holdings:
            raise ValueError(f"backtest {backtest_id} has no saved holding snapshot")

        security_ids: dict[str, int] = {}
        for ticker, _weight in holdings:
            cursor.execute(
                "INSERT INTO securities (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING",
                (ticker,),
            )
            cursor.execute("SELECT id FROM securities WHERE ticker = %s", (ticker,))
            security_ids[ticker] = cursor.fetchone()[0]

        sessions = nyse_sessions(start_date, end_date)["date"]
        if sessions.empty:
            raise ValueError(f"no trading sessions fall between {start_date} and {end_date}")
        first_session = sessions.iloc[0]
        pairs = [(first_session, first_session), *month_end_signals(start_date, end_date)]

        rows = []
        for signal_at, execution_at in pairs:
            signal_time = time(0, 0) if signal_at == execution_at else SIGNAL_TIME
            signal_stamp = signal_at.replace(
                hour=signal_time.hour, minute=signal_time.minute, tzinfo=SIGNAL_TIMEZONE
            ).to_pydatetime()
            for ticker, weight in holdings:
                rows.append((
                    backtest_id,
                    security_ids[ticker],
                    signal_stamp,
                    execution_at.date(),
                    weight,
                    PROVENANCE_ID,
                ))

        cursor.execute("DELETE FROM backtest_targets WHERE backtest_id = %s", (backtest_id,))
        cursor.executemany(
            """
            INSERT INTO backtest_targets
                (backtest_id, security_id, signal_at, execution_date, target_weight, provenance_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        return len(rows)
