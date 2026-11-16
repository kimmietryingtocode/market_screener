"""Tests for worker/risk.py. Pure math, no database or network."""

from __future__ import annotations

import unittest

from worker import risk


class HistoricalVarTests(unittest.TestCase):
    def test_picks_the_lower_tail_quantile(self) -> None:
        # 100 returns, -0.01 .. worst. At 95% the 5th percentile is the 5th worst.
        returns = [i / 1000.0 for i in range(-50, 50)]
        value = risk.historical_var(returns, confidence=0.95)
        self.assertAlmostEqual(value, -0.045, places=6)

    def test_is_returned_as_a_loss_not_a_magnitude(self) -> None:
        """Negative, so it sits on the same scale as max_drawdown rather
        than flipping sign halfway through the UI."""
        losses = [-0.05, -0.04, -0.03, -0.02, -0.01]
        self.assertLess(risk.historical_var(losses), 0)

    def test_a_higher_confidence_demands_a_worse_loss(self) -> None:
        returns = [i / 1000.0 for i in range(-50, 50)]
        self.assertLessEqual(
            risk.historical_var(returns, 0.99), risk.historical_var(returns, 0.95)
        )

    def test_needs_at_least_two_observations(self) -> None:
        self.assertIsNone(risk.historical_var([]))
        self.assertIsNone(risk.historical_var([0.01]))

    def test_ignores_nonfinite_values(self) -> None:
        returns = [-0.05, float("nan"), -0.01, None, 0.02]
        self.assertIsNotNone(risk.historical_var(returns))


class ConditionalVarTests(unittest.TestCase):
    def test_is_never_better_than_the_var_it_extends(self) -> None:
        """CVaR averages the losses beyond VaR, so it must be at least as
        bad."""
        returns = [i / 1000.0 for i in range(-50, 50)]
        self.assertLessEqual(
            risk.conditional_var(returns, 0.95), risk.historical_var(returns, 0.95)
        )

    def test_averages_the_tail_beyond_the_cutoff(self) -> None:
        # 20 observations, 95% -> cutoff index 1, so the tail is the worst one.
        returns = [-0.10] + [0.01] * 19
        self.assertAlmostEqual(risk.conditional_var(returns, 0.95), -0.10, places=6)

    def test_needs_at_least_two_observations(self) -> None:
        self.assertIsNone(risk.conditional_var([0.01]))


class BetaTests(unittest.TestCase):
    def test_moving_exactly_with_the_benchmark_is_one(self) -> None:
        market = [0.01, -0.02, 0.03, -0.01, 0.02]
        self.assertAlmostEqual(risk.beta(market, market), 1.0, places=9)

    def test_amplifying_the_benchmark_doubles_beta(self) -> None:
        market = [0.01, -0.02, 0.03, -0.01, 0.02]
        levered = [2 * r for r in market]
        self.assertAlmostEqual(risk.beta(levered, market), 2.0, places=9)

    def test_moving_against_the_benchmark_is_negative(self) -> None:
        market = [0.01, -0.02, 0.03, -0.01, 0.02]
        inverse = [-r for r in market]
        self.assertAlmostEqual(risk.beta(inverse, market), -1.0, places=9)

    def test_is_undefined_when_the_benchmark_never_moves(self) -> None:
        self.assertIsNone(risk.beta([0.01, 0.02, 0.03], [0.0, 0.0, 0.0]))

    def test_drops_pairs_where_either_side_is_missing(self) -> None:
        portfolio = [0.01, None, 0.03, float("nan"), 0.02]
        market = [0.01, 0.02, 0.03, 0.01, 0.02]
        self.assertIsNotNone(risk.beta(portfolio, market))


class ReturnsFromValuesTests(unittest.TestCase):
    def test_converts_a_value_series_to_simple_returns(self) -> None:
        got = risk.returns_from_values([100, 110, 121])
        self.assertEqual(len(got), 2)
        for value in got:
            self.assertAlmostEqual(value, 0.10, places=9)

    def test_one_value_yields_no_returns(self) -> None:
        self.assertEqual(risk.returns_from_values([100]), [])

    def test_skips_a_zero_denominator_rather_than_dividing_by_it(self) -> None:
        self.assertEqual(len(risk.returns_from_values([100, 0, 50])), 1)


if __name__ == "__main__":
    unittest.main()
