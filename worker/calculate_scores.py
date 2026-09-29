"""Turn price history and fundamentals into ranked scores.

momentum_score    percentile rank of trailing price return
fundamental_score percentile rank of a blend of revenue growth, operating
                  margin and (inverted) leverage
composite_score   weighted blend of the two

All three are 0-100 percentile ranks *within the scored universe*, so they
answer "how does this name compare to the others we track", not "is this
good in absolute terms". The raw inputs behind every score are written to
scores.components so a ranking can always be explained.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from datetime import date
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

METHODOLOGY_VERSION = "scores-v1"
MOMENTUM_LOOKBACK_SESSIONS = 126  # ~6 months of trading days
MOMENTUM_WEIGHT = 0.5
FUNDAMENTAL_WEIGHT = 0.5
# Which fundamentals feed the blend, and whether a bigger number is better.
FUNDAMENTAL_INPUTS = (
    ("revenue_growth", True),
    ("operating_margin", True),
    ("debt_to_equity", False),
)


def percentile_rank(values: dict[int, float], *, higher_is_better: bool) -> dict[int, float]:
    """Rank ids 0-100. A single value ranks 50 (nothing to compare against);
    ties share the average of their positions."""
    usable = {key: value for key, value in values.items() if value is not None and math.isfinite(value)}
    if not usable:
        return {}
    if len(usable) == 1:
        return {key: 50.0 for key in usable}

    ordered = sorted(usable.items(), key=lambda item: item[1])
    positions: dict[int, list[int]] = {}
    for index, (key, value) in enumerate(ordered):
        positions.setdefault(value, []).append(index)

    ranks: dict[int, float] = {}
    span = len(ordered) - 1
    for key, value in usable.items():
        average_index = sum(positions[value]) / len(positions[value])
        percentile = average_index / span * 100.0
        ranks[key] = percentile if higher_is_better else 100.0 - percentile
    return ranks


def momentum_returns(connection: Any, lookback: int = MOMENTUM_LOOKBACK_SESSIONS) -> dict[int, float]:
    """Trailing total return per security, from the most recent `lookback`
    sessions of price_bars."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH ranked AS (
                SELECT security_id, adjusted_close,
                       ROW_NUMBER() OVER (PARTITION BY security_id ORDER BY trading_date DESC) AS recency
                FROM price_bars
            )
            SELECT security_id,
                   MAX(adjusted_close) FILTER (WHERE recency = 1) AS latest,
                   MAX(adjusted_close) FILTER (WHERE recency = %s) AS earliest
            FROM ranked
            WHERE recency IN (1, %s)
            GROUP BY security_id
            """,
            (lookback, lookback),
        )
        returns = {}
        for security_id, latest, earliest in cursor.fetchall():
            if latest is None or earliest is None or float(earliest) <= 0:
                continue
            returns[security_id] = float(latest) / float(earliest) - 1.0
        return returns


def latest_fundamentals(connection: Any) -> dict[int, dict[str, float | None]]:
    """The most recent snapshot per security, by period_end."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (security_id)
                   security_id, revenue_growth, operating_margin, debt_to_equity
            FROM fundamental_snapshots
            ORDER BY security_id, period_end DESC, filing_date DESC
            """
        )
        result = {}
        for security_id, revenue_growth, operating_margin, debt_to_equity in cursor.fetchall():
            result[security_id] = {
                "revenue_growth": float(revenue_growth) if revenue_growth is not None else None,
                "operating_margin": float(operating_margin) if operating_margin is not None else None,
                "debt_to_equity": float(debt_to_equity) if debt_to_equity is not None else None,
            }
        return result


def build_scores(
    returns: dict[int, float],
    fundamentals: dict[int, dict[str, float | None]],
) -> dict[int, dict[str, Any]]:
    """Combine ranked momentum and ranked fundamentals into composites.

    A security needs a momentum figure to be scored at all (price history is
    the one input we always have). Missing fundamentals fall back to a
    neutral 50 rather than dropping the name or scoring it zero, which would
    misrepresent "unknown" as "bad".
    """
    momentum_ranks = percentile_rank(returns, higher_is_better=True)

    fundamental_ranks: dict[str, dict[int, float]] = {}
    for field, higher_is_better in FUNDAMENTAL_INPUTS:
        field_values = {
            security_id: values.get(field)
            for security_id, values in fundamentals.items()
            if values.get(field) is not None
        }
        fundamental_ranks[field] = percentile_rank(field_values, higher_is_better=higher_is_better)

    scores: dict[int, dict[str, Any]] = {}
    for security_id, momentum in momentum_ranks.items():
        present = [
            ranks[security_id]
            for ranks in fundamental_ranks.values()
            if security_id in ranks
        ]
        fundamental_score = sum(present) / len(present) if present else 50.0
        composite = MOMENTUM_WEIGHT * momentum + FUNDAMENTAL_WEIGHT * fundamental_score
        scores[security_id] = {
            "momentum_score": round(momentum, 4),
            "fundamental_score": round(fundamental_score, 4),
            "composite_score": round(composite, 4),
            "components": {
                "trailing_return": returns[security_id],
                "momentum_lookback_sessions": MOMENTUM_LOOKBACK_SESSIONS,
                "fundamental_inputs_used": len(present),
                **{field: fundamentals.get(security_id, {}).get(field) for field, _ in FUNDAMENTAL_INPUTS},
            },
        }
    return scores


def upsert_scores(connection: Any, scores: dict[int, dict[str, Any]], score_date: date) -> int:
    with connection.cursor() as cursor:
        for security_id, score in scores.items():
            cursor.execute(
                """
                INSERT INTO scores (
                    security_id, score_date, momentum_score, fundamental_score,
                    composite_score, components, methodology_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (security_id, score_date, methodology_version) DO UPDATE SET
                    momentum_score = EXCLUDED.momentum_score,
                    fundamental_score = EXCLUDED.fundamental_score,
                    composite_score = EXCLUDED.composite_score,
                    components = EXCLUDED.components
                """,
                (
                    security_id,
                    score_date,
                    score["momentum_score"],
                    score["fundamental_score"],
                    score["composite_score"],
                    json.dumps(score["components"]),
                    METHODOLOGY_VERSION,
                ),
            )
    return len(scores)


def calculate(connection: Any, score_date: date | None = None) -> int:
    score_date = score_date or date.today()
    returns = momentum_returns(connection)
    if not returns:
        raise ValueError(
            "no security has enough price history to score "
            f"({MOMENTUM_LOOKBACK_SESSIONS} sessions required) — run worker/ingest_prices.py first"
        )
    fundamentals = latest_fundamentals(connection)
    scores = build_scores(returns, fundamentals)
    return upsert_scores(connection, scores, score_date)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--score-date", help="YYYY-MM-DD; defaults to today")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="seconds between runs; omit to score once and exit",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    logging.basicConfig(level=logging.INFO)
    score_date = date.fromisoformat(args.score_date) if args.score_date else None
    with psycopg.connect(args.database_url, autocommit=True) as connection:
        while True:
            try:
                count = calculate(connection, score_date)
                logger.info("scored %s securities", count)
            except Exception:  # noqa: BLE001 - a bad run must not kill a scheduled loop
                logger.exception("scoring run failed")
            if args.interval_seconds is None:
                break
            time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
