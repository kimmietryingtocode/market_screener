"""Decide whether a backtest's inputs are complete enough to trust its output.

A run that exits zero only proves the engine didn't crash. It says nothing
about whether the data was there. Without this check, a backtest over a
window we have no prices for still "succeeds" — it just holds nothing and
reports a flat line, which looks like a real result.

So execution and trust are two separate axes. entrypoint.py already models
the trust axis as research_outcome; this module computes it from the data
instead of assuming it:

    blocked    a required security or the benchmark has no prices at all —
               nothing meaningful can be computed
    collecting prices exist but don't cover the requested window, or are
               missing on dates the strategy actually trades
    completed  every required security is priced across the whole window and
               on every execution date

Only `completed` is publishable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any


@dataclass
class CoverageReport:
    research_outcome: str
    publishable: bool
    messages: list[dict] = field(default_factory=list)
    available_at: str | None = None

    def add(self, code: str, severity: str, message: str) -> None:
        self.messages.append({"code": code, "severity": severity, "message": message})


def _required_tickers(connection: Any, backtest_id: int) -> tuple[list[str], str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT benchmark FROM backtests WHERE id = %s", (backtest_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"no backtest with id {backtest_id}")
        benchmark = row[0]

        cursor.execute(
            """
            SELECT DISTINCT s.ticker
            FROM backtest_targets t
            JOIN securities s ON s.id = t.security_id
            WHERE t.backtest_id = %s
            ORDER BY s.ticker
            """,
            (backtest_id,),
        )
        targets = [r[0] for r in cursor.fetchall()]
    return targets, benchmark


def assess(connection: Any, backtest_id: int) -> CoverageReport:
    """Grade the input data behind one backtest."""
    targets, benchmark = _required_tickers(connection, backtest_id)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT start_date, end_date FROM backtests WHERE id = %s",
            (backtest_id,),
        )
        start_date, end_date = cursor.fetchone()

        required = sorted(set(targets) | {benchmark})
        cursor.execute(
            """
            -- count(p.security_id), not count(*): with a LEFT JOIN the latter
            -- counts the securities row itself and so reports 1 bar for a
            -- security that has none.
            SELECT s.ticker, min(p.trading_date), max(p.trading_date), count(p.security_id)
            FROM securities s
            LEFT JOIN price_bars p ON p.security_id = s.id
            WHERE s.ticker = ANY(%s)
            GROUP BY s.ticker
            """,
            (required,),
        )
        spans = {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}

        # Execution dates the strategy actually trades on, and whether every
        # targeted security has a price that day. A missing price on a
        # rebalance date silently changes the portfolio the engine builds.
        cursor.execute(
            """
            SELECT t.execution_date, count(*) AS targets_due,
                   count(p.security_id) AS targets_priced
            FROM backtest_targets t
            LEFT JOIN price_bars p
                   ON p.security_id = t.security_id
                  AND p.trading_date = t.execution_date
            WHERE t.backtest_id = %s
            GROUP BY t.execution_date
            ORDER BY t.execution_date
            """,
            (backtest_id,),
        )
        execution_rows = cursor.fetchall()

    report = CoverageReport(research_outcome="completed", publishable=True)

    if not targets:
        report.research_outcome = "blocked"
        report.publishable = False
        report.add("NO_TARGETS", "error", "backtest has no target schedule to evaluate")
        return report

    # Nothing priced at all -> blocked.
    unpriced = [t for t in required if t not in spans or spans[t][1] is None]
    if unpriced:
        report.research_outcome = "blocked"
        report.publishable = False
        report.add(
            "NO_PRICE_DATA",
            "error",
            "no price history for: " + ", ".join(unpriced)
            + (f" (benchmark {benchmark} included)" if benchmark in unpriced else ""),
        )
        return report

    # Priced, but not across the whole requested window -> still collecting.
    for ticker in required:
        first, last, _count = spans[ticker]
        if first > start_date:
            report.research_outcome = "collecting"
            report.publishable = False
            report.add(
                "PRICE_HISTORY_STARTS_LATE",
                "warning",
                f"{ticker} prices begin {first}, after the requested start {start_date}",
            )
        if last < end_date:
            report.research_outcome = "collecting"
            report.publishable = False
            report.add(
                "PRICE_HISTORY_ENDS_EARLY",
                "warning",
                f"{ticker} prices end {last}, before the requested end {end_date}",
            )

    for execution_date, targets_due, targets_priced in execution_rows:
        if targets_priced < targets_due:
            report.research_outcome = "collecting"
            report.publishable = False
            report.add(
                "MISSING_EXECUTION_PRICE",
                "warning",
                f"{targets_due - targets_priced} of {targets_due} targets unpriced on execution date {execution_date}",
            )

    # available_at is a point-in-time claim: this result only became
    # computable once its newest input existed. Derive it from the data
    # rather than asserting the window's end date.
    newest = max(last for _first, last, _count in spans.values())
    report.available_at = datetime.combine(newest, time(0), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    if report.publishable:
        report.add("COVERAGE_COMPLETE", "info", f"all {len(required)} securities priced across {start_date}..{end_date}")

    return report
