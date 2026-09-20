"""Versioned NYSE session rules used by the backtest decision clock."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


CALENDAR_VERSION = "nyse-rules-2026-09-18"
CALENDAR_SOURCE = "https://www.nyse.com/trade/hours-calendars"
CALENDAR_START = date(2020, 1, 1)
CALENDAR_END = date(2026, 12, 31)
_SPECIAL_CLOSURES = {date(2025, 1, 9)}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    remainder = (32 + 2 * e + 2 * i - h - k) % 7
    correction = (a + 11 * h + 22 * remainder) // 451
    month = (h + remainder - 7 * correction + 114) // 31
    day = (h + remainder - 7 * correction + 114) % 31 + 1
    return date(year, month, day)


def _holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))
    return holidays | {day for day in _SPECIAL_CLOSURES if day.year == year}


def _early_closes(year: int) -> set[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {thanksgiving + timedelta(days=1)}
    july_third = date(year, 7, 3)
    christmas_eve = date(year, 12, 24)
    if july_third.weekday() < 5:
        candidates.add(july_third)
    if christmas_eve.weekday() < 5:
        candidates.add(christmas_eve)
    return candidates - _holidays(year)


def nyse_sessions(start: date, end: date) -> pd.DataFrame:
    """Return NYSE sessions and scheduled New York close times for a date range."""
    if end < start:
        raise ValueError("session end precedes start")
    if start < CALENDAR_START or end > CALENDAR_END:
        raise ValueError(
            f"NYSE calendar coverage is {CALENDAR_START} through {CALENDAR_END}"
        )
    holidays = set().union(*(_holidays(year) for year in range(start.year, end.year + 2)))
    early = set().union(*(_early_closes(year) for year in range(start.year, end.year + 1)))
    days = pd.date_range(start, end, freq="D")
    rows = []
    for stamp in days:
        day = stamp.date()
        if day.weekday() >= 5 or day in holidays:
            continue
        rows.append(
            {
                "date": stamp,
                "close_time": "13:00" if day in early else "16:00",
                "early_close": day in early,
                "calendar_version": CALENDAR_VERSION,
            }
        )
    return pd.DataFrame(rows)


def month_end_signals(start: date, end: date) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Return final monthly NYSE sessions and their following execution sessions."""
    sessions = nyse_sessions(start, end + timedelta(days=7))["date"]
    in_window = sessions[(sessions.dt.date >= start) & (sessions.dt.date <= end)]
    positions = {stamp: index for index, stamp in enumerate(sessions)}
    pairs: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for _, month in in_window.groupby([in_window.dt.year, in_window.dt.month]):
        signal = month.max()
        execution = sessions.iloc[positions[signal] + 1]
        if execution.date() <= end:
            pairs.append((signal, execution))
    return pairs
