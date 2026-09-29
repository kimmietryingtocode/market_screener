"""Expand a saved portfolio's static weights into a dated target schedule.

The API lets a user request a backtest of a portfolio they already saved
(ticker -> weight, no dates). The C++ engine needs a dated schedule instead:
a target weight per security at every rebalance point. This module is the
bridge — it holds the portfolio's weights constant and repeats them at every
month-end signal between the backtest's start and end dates, using the same
NYSE calendar (backtest.calendar.month_end_signals) the CSV-based smoke-test
schedule in backtest/make_test_targets.py already relies on.

Only monthly rebalancing is supported for now; that's the only frequency
month_end_signals implements.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
from zoneinfo import ZoneInfo

from backtest.calendar import month_end_signals

SIGNAL_TIME = time(18, 0)
SIGNAL_TIMEZONE = ZoneInfo("America/New_York")
PROVENANCE_ID = "portfolio-holdings"


def generate_targets_from_portfolio(connection: Any, backtest_id: int) -> int:
    """Populate backtest_targets for one queued backtest. Returns rows written.

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

        cursor.execute("SELECT ticker, weight FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
        holdings = cursor.fetchall()
        if not holdings:
            raise ValueError(f"portfolio {portfolio_id} has no holdings")

        security_ids: dict[str, int] = {}
        for ticker, _weight in holdings:
            cursor.execute(
                "INSERT INTO securities (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING",
                (ticker,),
            )
            cursor.execute("SELECT id FROM securities WHERE ticker = %s", (ticker,))
            security_ids[ticker] = cursor.fetchone()[0]

        pairs = month_end_signals(start_date, end_date)
        if not pairs:
            raise ValueError(f"no rebalance dates fall between {start_date} and {end_date}")

        rows = []
        for signal_at, execution_at in pairs:
            signal_stamp = signal_at.replace(
                hour=SIGNAL_TIME.hour, minute=SIGNAL_TIME.minute, tzinfo=SIGNAL_TIMEZONE
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
