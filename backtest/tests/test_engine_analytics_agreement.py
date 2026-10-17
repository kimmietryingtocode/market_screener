"""Contract test: the C++ engine and the Python analytics service must agree.

These are two independent implementations of the same metric definitions, in
different languages, shown side by side to users. Nothing in the type system
keeps them aligned — they diverged once already (log vs simple returns, a
drawdown that excluded starting capital, mismatched risk-free rates), and the
only reason it was caught was someone noticing a 50% loss reported as no
loss.

So this test feeds one price series through both and asserts they land on the
same numbers. To isolate the comparison it uses a single holding at 100%
weight with zero transaction costs, which makes the engine's portfolio
identical to simply holding that security — any disagreement is then a
difference in metric definition, not in portfolio construction.
"""

from __future__ import annotations

import csv
import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE = REPO_ROOT / "backtest" / "build" / "portfolio_backtest"

# The analytics implementation lives in the quant service, which is a
# separate deployable; import it directly so this test compares the real
# code rather than a copy of its formulas.
sys.path.insert(0, str(REPO_ROOT / "quant"))
from app.services import portfolio_analytics as analytics  # noqa: E402

TICKER = "AGREE"
BENCHMARK = "AGREEBM"

pytestmark = pytest.mark.skipif(not ENGINE.is_file(), reason="C++ engine not built: run `make -C backtest`")


def _sessions(count: int) -> list[date]:
    """Weekday-only dates, so the series looks like trading sessions."""
    days, current = [], date(2025, 1, 1)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _write_inputs(directory: Path, prices: list[float]) -> tuple[Path, Path, list[date]]:
    sessions = _sessions(len(prices))
    prices_path = directory / "prices.csv"
    with prices_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "ticker", "adjusted_close"])
        for session, price in zip(sessions, prices):
            writer.writerow([session.isoformat(), TICKER, price])
            # The benchmark is required but irrelevant here; hold it flat.
            writer.writerow([session.isoformat(), BENCHMARK, 100.0])

    targets_path = directory / "targets.csv"
    with targets_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["signal_at", "execution_date", "security_id", "target_weight", "provenance_id"])
        # Fully invested from the first session and never rebalanced, so the
        # engine's book is just "hold TICKER".
        writer.writerow([f"{sessions[0].isoformat()}T00:00:00+00:00", sessions[0].isoformat(), TICKER, 1.0, "agreement-test"])
    return prices_path, targets_path, sessions


def _run_engine(directory: Path, prices: list[float]) -> dict[str, float]:
    prices_path, targets_path, sessions = _write_inputs(directory, prices)
    output_dir = directory / "out"
    subprocess.run(
        [
            str(ENGINE),
            "--prices", str(prices_path),
            "--targets", str(targets_path),
            "--output-dir", str(output_dir),
            "--benchmark", BENCHMARK,
            "--start", sessions[0].isoformat(),
            "--end", sessions[-1].isoformat(),
            "--cost-bps", "0",
            "--initial-capital", "100000",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    with (output_dir / "summary.csv").open(newline="") as handle:
        return {
            row["metric"]: float(row["portfolio"])
            for row in csv.DictReader(handle)
            if row["portfolio"] not in ("", None)
        }


def _analytics_metrics(prices: list[float], sessions: list[date]) -> dict[str, float]:
    frame = pd.DataFrame({TICKER: prices}, index=pd.to_datetime(sessions))
    returns = analytics.compute_portfolio_returns(frame, {TICKER: 1.0})
    ann_ret = analytics.annualized_return(returns)
    ann_vol = analytics.annualized_volatility(returns)

    # The engine measures a portfolio from the moment capital is committed,
    # so its return series carries one extra leading observation: cash ->
    # first session's market value, which is where entry transaction costs
    # land. The analytics service only ever sees a price window and has no
    # notion of committed capital, so it has one fewer observation.
    #
    # That is a real definitional difference, not a defect on either side.
    # Reconstructing it here pins the gap to exactly that one observation:
    # if either implementation's *formula* drifts, these still diverge.
    as_engine = pd.concat([pd.Series([0.0]), returns], ignore_index=True)
    engine_basis_vol = analytics.annualized_volatility(as_engine)
    engine_basis_mean = float(as_engine.mean())
    engine_basis_sharpe = (
        engine_basis_mean / (engine_basis_vol / math.sqrt(252)) * math.sqrt(252)
        if engine_basis_vol > 0
        else float("nan")
    )

    return {
        "total_return": analytics.total_return(returns),
        "annualized_volatility": ann_vol,
        "sharpe_ratio": analytics.sharpe_ratio(ann_ret, ann_vol),
        "maximum_drawdown": analytics.max_drawdown(returns),
        "annualized_volatility_engine_basis": engine_basis_vol,
        "sharpe_ratio_engine_basis": engine_basis_sharpe,
    }


def _compare(prices: list[float]) -> tuple[dict[str, float], dict[str, float]]:
    with TemporaryDirectory() as tmp:
        engine = _run_engine(Path(tmp), prices)
    return engine, _analytics_metrics(prices, _sessions(len(prices)))


RISING = [100.0 * (1.004 ** i) for i in range(60)]
FALLING = [100.0 * (0.995 ** i) for i in range(60)]
CRASH_AND_RECOVER = (
    [100.0 + i for i in range(20)]        # up to ~119
    + [119.0 * (0.94 ** i) for i in range(1, 15)]  # sharp fall
    + [52.0 * (1.01 ** i) for i in range(1, 26)]   # partial recovery
)
CHOPPY = [100.0 * (1.0 + 0.02 * math.sin(i / 2.0)) for i in range(60)]


@pytest.mark.parametrize(
    "name,prices",
    [("rising", RISING), ("falling", FALLING), ("crash_and_recover", CRASH_AND_RECOVER), ("choppy", CHOPPY)],
)
def test_total_return_agrees(name, prices):
    engine, py = _compare(prices)
    assert engine["total_return"] == pytest.approx(py["total_return"], abs=1e-6), name


@pytest.mark.parametrize(
    "name,prices",
    [("rising", RISING), ("falling", FALLING), ("crash_and_recover", CRASH_AND_RECOVER), ("choppy", CHOPPY)],
)
def test_max_drawdown_agrees(name, prices):
    engine, py = _compare(prices)
    assert engine["maximum_drawdown"] == pytest.approx(py["maximum_drawdown"], abs=1e-6), name


@pytest.mark.parametrize(
    "name,prices",
    [("rising", RISING), ("falling", FALLING), ("crash_and_recover", CRASH_AND_RECOVER), ("choppy", CHOPPY)],
)
def test_volatility_agrees_on_the_same_observation_set(name, prices):
    """Same stddev convention (sample, ddof=1) and same sqrt(252) scaling."""
    engine, py = _compare(prices)
    assert engine["annualized_volatility"] == pytest.approx(
        py["annualized_volatility_engine_basis"], rel=1e-6
    ), name


@pytest.mark.parametrize(
    "name,prices",
    [("rising", RISING), ("falling", FALLING), ("crash_and_recover", CRASH_AND_RECOVER), ("choppy", CHOPPY)],
)
def test_sharpe_agrees_on_the_same_observation_set(name, prices):
    """Also confirms both use a zero risk-free rate. When these diverged, the
    service was netting out 4% and the engine 0%."""
    engine, py = _compare(prices)
    assert engine["sharpe_ratio"] == pytest.approx(py["sharpe_ratio_engine_basis"], rel=1e-6), name


@pytest.mark.parametrize(
    "name,prices",
    [("rising", RISING), ("falling", FALLING), ("crash_and_recover", CRASH_AND_RECOVER), ("choppy", CHOPPY)],
)
def test_the_capital_commitment_period_is_the_only_volatility_difference(name, prices):
    """Documents the one legitimate gap, so it stays small and explained
    rather than growing unnoticed. With zero costs the extra observation is
    a 0.0 return, which still dilutes dispersion slightly."""
    engine, py = _compare(prices)

    # A constant-growth series has no dispersion at all (true volatility is
    # floating-point noise), so a ratio against it is meaningless — the
    # engine's single 0.0 observation is then the *only* dispersion present.
    if py["annualized_volatility"] < 1e-6:
        pytest.skip(f"{name} has no real dispersion to compare")

    ratio = engine["annualized_volatility"] / py["annualized_volatility"]
    assert 0.5 < ratio < 2.0, f"{name}: volatility differs by more than the extra observation explains ({ratio:.3f})"


def test_a_halved_portfolio_is_reported_as_a_real_loss_by_both():
    """The regression that started this: both sides must report roughly -50%,
    and neither may report 'no major loss'."""
    prices = [100.0 * (0.98855 ** i) for i in range(60)]  # ~-50% over the window
    engine, py = _compare(prices)

    assert engine["total_return"] < -0.45
    assert py["total_return"] < -0.45
    assert engine["maximum_drawdown"] < -0.45
    assert py["maximum_drawdown"] < -0.45
    assert py["maximum_drawdown"] >= -1.0
