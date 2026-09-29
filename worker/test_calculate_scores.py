"""Tests for worker/calculate_scores.py.

The ranking math is pure, so most of this runs with no database at all.
"""

from __future__ import annotations

import math
import os
import unittest
from datetime import date, timedelta

from worker import calculate_scores

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


def _db_available() -> bool:
    if not TEST_DATABASE_URL or psycopg is None:
        return False
    try:
        with psycopg.connect(TEST_DATABASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()


class PercentileRankTests(unittest.TestCase):
    def test_best_and_worst_anchor_the_range(self) -> None:
        ranks = calculate_scores.percentile_rank({1: 0.1, 2: 0.5, 3: 0.9}, higher_is_better=True)
        self.assertEqual(ranks[3], 100.0)
        self.assertEqual(ranks[1], 0.0)
        self.assertEqual(ranks[2], 50.0)

    def test_lower_is_better_inverts_the_scale(self) -> None:
        ranks = calculate_scores.percentile_rank({1: 0.1, 2: 0.9}, higher_is_better=False)
        self.assertEqual(ranks[1], 100.0)  # least leverage ranks best
        self.assertEqual(ranks[2], 0.0)

    def test_single_value_is_neutral_not_perfect(self) -> None:
        """With nothing to compare against, 100 would overstate it."""
        ranks = calculate_scores.percentile_rank({7: 0.42}, higher_is_better=True)
        self.assertEqual(ranks, {7: 50.0})

    def test_ties_share_the_average_position(self) -> None:
        ranks = calculate_scores.percentile_rank({1: 5.0, 2: 5.0, 3: 9.0}, higher_is_better=True)
        self.assertEqual(ranks[1], ranks[2])
        self.assertGreater(ranks[3], ranks[1])

    def test_none_and_nonfinite_values_are_excluded(self) -> None:
        ranks = calculate_scores.percentile_rank(
            {1: 0.1, 2: None, 3: math.nan, 4: 0.9}, higher_is_better=True
        )
        self.assertEqual(set(ranks), {1, 4})

    def test_empty_input_produces_no_ranks(self) -> None:
        self.assertEqual(calculate_scores.percentile_rank({}, higher_is_better=True), {})


class BuildScoresTests(unittest.TestCase):
    def test_missing_fundamentals_score_neutral_not_zero(self) -> None:
        """Unknown must not be punished as if it were bad."""
        returns = {1: 0.5, 2: 0.1}
        fundamentals = {1: {"revenue_growth": 0.3, "operating_margin": 0.2, "debt_to_equity": 0.1}}

        scores = calculate_scores.build_scores(returns, fundamentals)

        self.assertEqual(scores[2]["fundamental_score"], 50.0)
        self.assertEqual(scores[2]["components"]["fundamental_inputs_used"], 0)

    def test_composite_is_the_weighted_blend(self) -> None:
        returns = {1: 0.5, 2: 0.1}
        scores = calculate_scores.build_scores(returns, {})

        for score in scores.values():
            expected = (
                calculate_scores.MOMENTUM_WEIGHT * score["momentum_score"]
                + calculate_scores.FUNDAMENTAL_WEIGHT * score["fundamental_score"]
            )
            self.assertAlmostEqual(score["composite_score"], round(expected, 4))

    def test_a_security_without_price_history_is_not_scored(self) -> None:
        """Momentum is the one required input — fundamentals alone isn't a score."""
        scores = calculate_scores.build_scores({}, {99: {"revenue_growth": 0.5}})
        self.assertEqual(scores, {})

    def test_leverage_is_inverted_so_less_debt_ranks_higher(self) -> None:
        returns = {1: 0.2, 2: 0.2}
        fundamentals = {
            1: {"debt_to_equity": 0.1},  # less levered
            2: {"debt_to_equity": 2.0},
        }

        scores = calculate_scores.build_scores(returns, fundamentals)

        self.assertGreater(scores[1]["fundamental_score"], scores[2]["fundamental_score"])

    def test_components_record_the_raw_inputs_for_explainability(self) -> None:
        returns = {1: 0.25}
        fundamentals = {1: {"revenue_growth": 0.3, "operating_margin": 0.2, "debt_to_equity": 0.4}}

        scores = calculate_scores.build_scores(returns, fundamentals)

        components = scores[1]["components"]
        self.assertEqual(components["trailing_return"], 0.25)
        self.assertEqual(components["revenue_growth"], 0.3)
        self.assertEqual(components["momentum_lookback_sessions"], calculate_scores.MOMENTUM_LOOKBACK_SESSIONS)


@unittest.skipUnless(DB_AVAILABLE, "TEST_DATABASE_URL not set or unreachable")
class CalculateAgainstDatabaseTests(unittest.TestCase):
    TICKERS = ("ZSCOREA", "ZSCOREB")

    def setUp(self) -> None:
        self.connection = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(self.connection.close)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM securities WHERE ticker = ANY(%s)", (list(self.TICKERS),))

    def test_momentum_returns_needs_the_full_lookback_window(self) -> None:
        """A security with fewer than MOMENTUM_LOOKBACK_SESSIONS bars has no
        comparable starting price, so it must be left out rather than scored
        off a shorter window."""
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO securities (ticker) VALUES (%s) RETURNING id", (self.TICKERS[0],))
            security_id = cursor.fetchone()[0]
            rows = [(security_id, date(2025, 1, 1) + timedelta(days=i), 100.0 + i) for i in range(10)]
            cursor.executemany(
                "INSERT INTO price_bars (security_id, trading_date, adjusted_close) VALUES (%s, %s, %s)",
                rows,
            )

        returns = calculate_scores.momentum_returns(self.connection, lookback=126)

        self.assertNotIn(security_id, returns)

    def test_momentum_returns_computes_over_the_available_window(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO securities (ticker) VALUES (%s) RETURNING id", (self.TICKERS[1],))
            security_id = cursor.fetchone()[0]
            # 5 sessions: 100 -> 200 across a lookback of 5
            prices = [100.0, 120.0, 140.0, 160.0, 200.0]
            cursor.executemany(
                "INSERT INTO price_bars (security_id, trading_date, adjusted_close) VALUES (%s, %s, %s)",
                [(security_id, date(2025, 3, 1) + timedelta(days=i), p) for i, p in enumerate(prices)],
            )

        returns = calculate_scores.momentum_returns(self.connection, lookback=5)

        self.assertAlmostEqual(returns[security_id], 1.0)  # 100 -> 200


if __name__ == "__main__":
    unittest.main()
