from __future__ import annotations

import json
import hashlib
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from backtest.calendar import month_end_signals, nyse_sessions
from backtest.workflow import (
    DEFAULT_CONFIG,
    _approval_at,
    _approval_state,
    build_targets,
    forward,
    review_summary,
    select_fundamental_snapshots,
)


def test_nested_review_approval_is_discovered(tmp_path):
    package = tmp_path / "run" / "top10_review" / "factset" / "import-id"
    review = package / "reviews" / "review-1"
    review.mkdir(parents=True)
    (review / "manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "reviewed_at": "2026-09-18T05:09:37+00:00",
                "expires_at": "2027-01-16",
            }
        )
    )
    pd.DataFrame({"factset_symbol": ["AAPL-US"], "decision": ["approve"]}).to_csv(
        review / "decisions.csv", index=False
    )

    assert _approval_state(tmp_path, datetime(2026, 9, 18, 6, tzinfo=UTC))[0] == {"AAPL"}
    assert not _approval_state(tmp_path, datetime(2026, 9, 18, 5, tzinfo=UTC))[0]
    assert _approval_at(tmp_path, datetime(2026, 9, 18, 5, tzinfo=UTC)) is None

    rejected = package / "reviews" / "review-2"
    rejected.mkdir()
    (rejected / "manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "reviewed_at": "2026-09-19T05:09:37+00:00",
                "expires_at": "2027-01-16",
            }
        )
    )
    pd.DataFrame({"factset_symbol": ["AAPL-US"], "decision": ["reject"]}).to_csv(
        rejected / "decisions.csv", index=False
    )
    assert not _approval_state(tmp_path, datetime(2026, 9, 20, tzinfo=UTC))[0]
    assert _approval_at(tmp_path, datetime(2026, 9, 20, tzinfo=UTC)) == set()


def test_forward_rejects_future_cutoff():
    class Args:
        config = DEFAULT_CONFIG
        as_of = "9999-01-01T00:00:00+00:00"

    with pytest.raises(ValueError, match="cannot be in the future"):
        forward(Args())


def test_forward_reports_collecting_before_price_validation(tmp_path, monkeypatch):
    cutoff = datetime.now(UTC) - timedelta(minutes=1)
    cache = tmp_path / "cache"
    cache.mkdir()
    pd.DataFrame(
        [{"ticker": "AAA", "company": "Alpha", "sector": "Technology", "industry": "Software", "sector_etf": "XLK"}]
    ).to_csv(cache / "universe.csv", index=False)
    pd.DataFrame(
        [{"date": "2026-01-02", "ticker": ticker, "adjusted_close": 100.0} for ticker in ("AAA", "XLK", "SPY")]
    ).to_csv(cache / "prices.csv", index=False)
    (cache / "data_manifest.json").write_text(
        json.dumps({"created_at": (cutoff - timedelta(minutes=1)).isoformat()})
    )

    class Args:
        config = DEFAULT_CONFIG
        as_of = cutoff.isoformat()
        cache_dir = cache
        screener_root = tmp_path / "reviews"
        output_root = tmp_path / "runs"
        start = cutoff.date().isoformat()
        end = cutoff.date().isoformat()
        strategy = "primary"

    monkeypatch.setattr(
        "backtest.workflow.validate_prices",
        lambda *args, **kwargs: pytest.fail("collecting state must precede price validation"),
    )
    assert forward(Args()) == 0
    run = next((tmp_path / "runs").iterdir())
    status = json.loads((run / "status.json").read_text())
    assert status["status"] == "collecting"
    assert status["reasons"] == ["no_eligible_explicit_summary_review"]


def test_rank_before_manual_approval_does_not_substitute(tmp_path):
    dates = pd.bdate_range("2022-01-03", "2023-03-31")
    rows = []
    growth = {"SPY": 1.0001, "XLV": 1.0002, "AAA": 1.0020, "BBB": 1.0005}
    for idx, day in enumerate(dates):
        for ticker, rate in growth.items():
            rows.append(
                {
                    "date": day.date().isoformat(),
                    "ticker": ticker,
                    "adjusted_close": 100 * (rate**idx),
                }
            )
    prices_path = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(prices_path, index=False)
    universe_path = tmp_path / "universe.csv"
    pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "company": "Alpha",
                "sector": "Healthcare",
                "industry": "Biotech",
                "sector_etf": "XLV",
            },
            {
                "ticker": "BBB",
                "company": "Beta",
                "sector": "Healthcare",
                "industry": "Biotech",
                "sector_etf": "XLV",
            },
        ]
    ).to_csv(universe_path, index=False)
    config = {
        "score_weights": {
            "rs_3m": 0.4,
            "return_6m": 0.3,
            "return_1m": 0.2,
            "low_volatility": 0.1,
        },
        "sector_scan": {"top_n": 1},
        "industry_scan": {"top_n_per_sector": 1},
        "company_scan": {"top_n_per_industry": 1},
        "backtest": {
            "benchmark": "SPY",
            "trend_days": 20,
            "maximum_position_weight": 0.15,
            "maximum_sector_weight": 0.40,
            "weighting": "inverse-vol",
            "timezone": "America/New_York",
            "signal_time": "18:00",
        },
    }

    targets, _, eligibility = build_targets(
        prices_path,
        universe_path,
        config,
        start=date(2023, 1, 1),
        end=date(2023, 3, 31),
        approval_tickers={"BBB"},
        label="test",
    )

    assert set(targets["security_id"]) == {""}
    assert "manual_summary_unapproved" in set(eligibility["reason"])


def test_sec_gate_uses_only_fundamentals_available_at_signal(tmp_path):
    dates = pd.bdate_range("2022-01-03", "2023-03-31")
    rows = [
        {"date": day.date().isoformat(), "ticker": ticker, "adjusted_close": 100 * rate**idx}
        for idx, day in enumerate(dates)
        for ticker, rate in {"SPY": 1.0001, "XLV": 1.0002, "AAA": 1.002}.items()
    ]
    prices = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(prices, index=False)
    universe = tmp_path / "universe.csv"
    pd.DataFrame([{"ticker": "AAA", "company": "Alpha", "sector": "Healthcare", "industry": "Biotech", "sector_etf": "XLV"}]).to_csv(universe, index=False)
    config = {
        "score_weights": {"rs_3m": 0.4, "return_6m": 0.3, "return_1m": 0.2, "low_volatility": 0.1},
        "sector_scan": {"top_n": 1},
        "industry_scan": {"top_n_per_sector": 1},
        "company_scan": {"top_n_per_industry": 1},
        "backtest": {"benchmark": "SPY", "trend_days": 0, "maximum_position_weight": 1.0, "maximum_sector_weight": 1.0, "weighting": "equal", "timezone": "America/New_York", "signal_time": "18:00"},
    }
    fundamentals = pd.DataFrame([{"ticker": "AAA", "period_end": "2022-12-31", "available_at": "2023-02-15T21:00:00Z", "ebitda_ttm": 1.0, "free_cash_flow_ttm": 1.0}])

    targets, _, eligibility = build_targets(
        prices,
        universe,
        config,
        start=date(2023, 1, 1),
        end=date(2023, 3, 31),
        approval_tickers=None,
        label="test",
        fundamentals=fundamentals,
    )

    assert list(targets["security_id"]) == ["", "AAA"]
    assert list(eligibility["reason"]) == ["sec_unavailable", "eligible"]


def test_sec_snapshot_selects_period_before_revision_and_ignores_future_rows():
    cutoff = pd.Timestamp("2023-11-01T22:00:00Z")
    rows = pd.DataFrame(
        [
            {"ticker": "AAA", "period_end": "2023-03-31", "fiscal_period": "Q1", "available_at": "2023-05-01T20:00:00Z", "as_of": "2023-06-01T00:00:00Z", "ebitda_ttm": 1, "free_cash_flow_ttm": 1, "accession": "old"},
            {"ticker": "AAA", "period_end": "2023-03-31", "fiscal_period": "Q1", "available_at": "2023-10-01T20:00:00Z", "as_of": "2023-10-02T00:00:00Z", "ebitda_ttm": 99, "free_cash_flow_ttm": 99, "accession": "old-restatement"},
            {"ticker": "AAA", "period_end": "2023-06-30", "fiscal_period": "Q2", "available_at": "2023-08-01T20:00:00Z", "as_of": "2023-08-02T00:00:00Z", "ebitda_ttm": 2, "free_cash_flow_ttm": 2, "accession": "latest-period"},
            {"ticker": "AAA", "period_end": "2023-09-30", "fiscal_period": "Q3", "available_at": "2023-10-15T20:00:00Z", "as_of": "2023-12-01T00:00:00Z", "ebitda_ttm": 3, "free_cash_flow_ttm": 3, "accession": "future-snapshot"},
        ]
    )
    selected = select_fundamental_snapshots(rows, cutoff, ["AAA", "BBB"])
    assert selected.loc[selected["ticker"].eq("AAA"), "accession"].iloc[0] == "latest-period"
    assert selected.loc[selected["ticker"].eq("BBB"), "fundamental_status"].iloc[0] == "unavailable"

    before = select_fundamental_snapshots(rows.iloc[:3], cutoff, ["AAA"])
    after = select_fundamental_snapshots(rows, cutoff, ["AAA"])
    pd.testing.assert_frame_equal(
        before.reset_index(drop=True), after[before.columns].reset_index(drop=True)
    )


def test_nyse_calendar_controls_holidays_early_closes_and_next_session():
    sessions = nyse_sessions(date(2024, 7, 1), date(2024, 7, 5))
    assert list(sessions["date"].dt.strftime("%Y-%m-%d")) == [
        "2024-07-01", "2024-07-02", "2024-07-03", "2024-07-05"
    ]
    july_third = sessions[sessions["date"].dt.date.eq(date(2024, 7, 3))].iloc[0]
    assert july_third["early_close"]
    assert july_third["close_time"] == "13:00"
    pairs = month_end_signals(date(2024, 5, 1), date(2024, 6, 5))
    assert (pairs[0][0].date(), pairs[0][1].date()) == (
        date(2024, 5, 31), date(2024, 6, 3)
    )
    with pytest.raises(ValueError, match="calendar coverage"):
        nyse_sessions(date(2027, 1, 1), date(2027, 1, 2))


def test_summary_review_is_complete_hashed_and_requires_override_reason(tmp_path):
    package = tmp_path / "run" / "factset" / "import-id"
    package.mkdir(parents=True)
    queue = tmp_path / "queue.csv"
    pd.DataFrame({"factset_symbol": ["AAA-US", "BBB-US"]}).to_csv(queue, index=False)
    queue_hash = hashlib.sha256(queue.read_bytes()).hexdigest()
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "import_id": "import-id",
                "expires_at": "2099-01-01",
                "shortlist": {"path": str(queue), "sha256": queue_hash},
            }
        )
    )
    pd.DataFrame(
        columns=["source_file", "identifier", "severity", "code", "field", "message"]
    ).to_csv(package / "validation_report.csv", index=False)
    pd.DataFrame({"factset_symbol": ["AAA-US"]}).to_csv(
        package / "approved_candidates.csv", index=False
    )
    decisions = tmp_path / "decisions.csv"
    pd.DataFrame(
        {
            "factset_symbol": ["AAA-US", "BBB-US"],
            "decision": ["approve", "reject"],
            "reason": ["", "quality gate failed"],
        }
    ).to_csv(decisions, index=False)
    output = review_summary(package, decisions)
    assert (output / "manifest.json").is_file()
    assert _approval_state(tmp_path, datetime.now(UTC))[0] == {"AAA"}

    pd.DataFrame(
        {
            "factset_symbol": ["AAA-US", "BBB-US"],
            "decision": ["approve", "approve"],
            "reason": ["", ""],
        }
    ).to_csv(decisions, index=False)
    with pytest.raises(ValueError, match="nonblank reason"):
        review_summary(package, decisions)
    queue.write_text("factset_symbol\nTAMPERED-US\n")
    with pytest.raises(ValueError, match="queue hash"):
        review_summary(package, decisions)
