from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.fundamentals.api import compare_frames, validate_snapshot, write_parquet
from backtest.fundamentals.core import canonical, utc
from backtest.fundamentals.edgar import availability, filing_records, normalize_edgar
from backtest.fundamentals.http import JsonClient
from backtest.fundamentals.prices import PriceQuote, enrich_prices

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "edgar.json").read_text())
CUTOFF = utc("2024-02-16T12:00:00Z")


def normalize(ticker="KO", cutoff=CUTOFF, policy="latest", payload=None):
    return normalize_edgar(payload or deepcopy(FIXTURE["companies"][ticker]), FIXTURE["filings"], ticker, as_of=cutoff, policy=policy)


def row(frame, period="FY", end="2023-12-31"):
    return frame[(frame.fiscal_period == period) & (frame.period_end == end)].iloc[0]


@pytest.mark.parametrize("ticker,end,tag,value", [
    ("AAPL", "2022-09-24", "SalesRevenueNet", 400),
    ("AAPL", "2023-09-30", "RevenueFromContractWithCustomerExcludingAssessedTax", 460),
    ("MSFT", "2023-06-30", "RevenueFromContractWithCustomerIncludingAssessedTax", 300),
    ("KO", "2023-12-31", "Revenues", 520),
])
def test_tag_fallback_is_per_period(ticker, end, tag, value):
    frame, _, _ = normalize(ticker)
    actual = row(frame, end=end)
    assert actual.revenue == value
    assert actual.revenue_tag == tag


def test_q4_cash_flows_and_annual_do_not_mix():
    frame, _, _ = normalize()
    fourth = row(frame, "Q4")
    assert fourth.revenue == 160
    assert fourth.operating_cash_flow == 25
    assert fourth.capex == 5
    assert fourth.free_cash_flow == 20
    assert fourth.ebitda == 31
    assert fourth.ebitda_ttm == 94
    assert fourth.free_cash_flow_ttm == 56
    assert fourth.revenue_derived
    assert fourth.period_start == pd.Timestamp("2023-10-01")
    assert row(frame).operating_cash_flow == 70
    assert pd.isna(fourth.diluted_eps)
    assert pd.isna(fourth.diluted_shares)
    assert row(frame).diluted_eps == 5.2
    assert fourth.roe == pytest.approx(52 / 120)
    assert fourth.roa == pytest.approx(52 / 225)
    assert fourth.asset_turnover == pytest.approx(520 / 225)
    validate_snapshot(frame, CUTOFF)


def test_53_week_non_calendar_fiscal_year():
    frame, _, _ = normalize("AAPL")
    fourth = row(frame, "Q4", "2023-09-30")
    assert fourth.revenue == 130
    assert fourth.period_start == pd.Timestamp("2023-07-02")
    assert fourth.fiscal_year == 2023


def test_restatement_policy_and_audit():
    early, _, _ = normalize()
    latest, superseded, archive = normalize(cutoff="2025-03-01T00:00:00Z")
    earliest, _, _ = normalize(cutoff="2025-03-01T00:00:00Z", policy="earliest")
    assert row(early).revenue == row(earliest).revenue == 520
    assert row(latest).revenue == 540
    assert 520 in superseded.value.values
    assert 540 in archive.value.values
    assert pd.isna(row(latest, "Q4").revenue)


@pytest.mark.parametrize("cutoff", ["2024-01-31T23:00:00Z", "2024-02-15T21:00:00Z"])
def test_february_and_after_close_filings_cannot_reach_earlier_decisions(cutoff):
    frame, _, _ = normalize(cutoff=cutoff)
    assert not frame.period_end.eq(pd.Timestamp("2023-12-31")).any()
    assert frame.loc[frame.fiscal_period != "FY", "revenue_ttm"].isna().all()


def test_adding_future_facts_tags_and_metadata_cannot_change_past():
    base = deepcopy(FIXTURE["companies"]["KO"])
    base["facts"]["us-gaap"]["Revenues"]["units"]["USD"].pop()
    expected, _, _ = normalize(payload=base)
    future = deepcopy(FIXTURE["companies"]["KO"])
    future["facts"]["us-gaap"]["SalesRevenueNet"] = {"units": {"USD": [{"start": "2020-01-01", "end": "2020-12-31", "val": 999, "filed": "2025-02-15", "accn": "revision", "form": "10-K", "fy": 2024, "fp": "FY"}]}}
    actual, _, _ = normalize(payload=future)
    pd.testing.assert_frame_equal(expected, actual)


def test_missing_components_never_become_zero():
    payload = deepcopy(FIXTURE["companies"]["KO"])
    payload["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"].pop(2)
    frame, _, _ = normalize(payload=payload)
    assert pd.isna(row(frame, "Q4").operating_cash_flow)
    assert pd.isna(row(frame, "Q4").free_cash_flow)
    assert pd.isna(row(frame).total_debt)


def test_date_only_filing_and_timezone_validation():
    stamp, precision = availability({"filed": "2024-02-15"}, {})
    assert stamp == utc("2024-02-16T05:00:00Z")
    assert precision == "date_only_delayed"
    with pytest.raises(ValueError, match="timezone"):
        utc("2024-02-15")
    assert pd.isna(availability({}, {})[0])
    assert filing_records({"accessionNumber": ["a"], "reportDate": ["2023-12-31"]})["a"]["reportDate"] == "2023-12-31"


def test_price_availability_and_no_weighted_average_market_cap():
    frame, _, _ = normalize()
    class Prices:
        name = "fixture"
        def __init__(self, stamp):
            self.stamp = stamp
        def quote(self, ticker, filing_date, *, as_of):
            return PriceQuote(20, pd.Timestamp(filing_date), utc(self.stamp), "USD", pd.Timestamp("2020-01-01"))
    enriched = enrich_prices(frame, Prices("2024-02-15T21:00:00Z"))
    annual = row(enriched)
    assert annual.market_cap == 240  # 12 actual outstanding, not 10 diluted average.
    assert annual.pe == pytest.approx(20 / 5.2)
    assert annual.pb == pytest.approx(240 / 140)
    validate_snapshot(enriched, CUTOFF)
    future = enrich_prices(frame, Prices("2024-02-17T21:00:00Z"))
    assert future.price.isna().all()


def test_summary_comparison_preserves_conflicts():
    shared = {
        "ticker": "KO", "period_start": "2023-01-01", "period_end": "2023-12-31",
        "fiscal_period": "FY", "currency": "USD", "revenue_unit": "USD",
        "revenue_basis": "period", "available_at": "2024-02-01T12:00:00Z",
    }
    edgar = pd.DataFrame([{**shared, "revenue": 520.0}])
    summary = pd.DataFrame([{**shared, "revenue": 525.2, "is_actual": True}])
    compared = compare_frames(edgar, summary, as_of=CUTOFF, tolerance=0.009)
    vendor = compared[compared.source == "summary"].iloc[0]
    assert vendor.edgar_value == 520
    assert vendor.spread_pct == pytest.approx(1)
    assert vendor.flagged
    boundary = compare_frames(edgar, summary, as_of=CUTOFF)
    assert not boundary[boundary.source == "summary"].iloc[0].flagged


def test_parquet_round_trip(tmp_path):
    frame, superseded, archive = normalize()
    for name, data in (("model", frame), ("superseded", superseded), ("archive", archive), ("empty", canonical())):
        target = tmp_path / f"{name}.parquet"
        write_parquet(data, target)
        pd.testing.assert_frame_equal(data.reset_index(drop=True), pd.read_parquet(target))


def test_cache_retries_and_versions(tmp_path, monkeypatch):
    client = JsonClient(tmp_path, user_agent="Test test@example.com")
    calls = []
    class FakeResponse:
        headers = {"Retry-After": "0"}
        def __init__(self, status, value):
            self.status_code, self.value = status, value
        def json(self):
            return {"value": self.value}
    replies = iter([FakeResponse(429, 0), FakeResponse(503, 0), FakeResponse(200, 1), FakeResponse(200, 2)])
    def get(*args, **kwargs):
        calls.append(kwargs)
        return next(replies)
    monkeypatch.setattr(client.session, "get", get)
    monkeypatch.setattr("backtest.fundamentals.http.time.sleep", lambda _: None)
    url = "https://data.sec.gov/test"
    first = client.get(url)
    assert len(calls) == 3
    assert calls[0]["headers"]["User-Agent"] == "Test test@example.com"
    assert client.get(url).data == {"value": 1}
    client.refresh = True
    client._refreshed.clear()
    later = client.get(url)
    assert len(client.snapshots(url + "{}")) == 2
    assert later.observed_at >= first.observed_at
    client.refresh = False
    assert client.get(url, as_of=first.observed_at).data == {"value": 1}
    with pytest.raises(ValueError, match="cutoff"):
        client.get(url, as_of=CUTOFF)


@pytest.mark.parametrize("reference", [0, None])
def test_comparison_does_not_invent_reference_values(reference):
    shared = {
        "ticker": "KO", "period_start": "2023-01-01", "period_end": "2023-12-31",
        "fiscal_period": "FY", "currency": "USD", "revenue_unit": "USD",
        "revenue_basis": "period", "available_at": "2024-02-01T12:00:00Z",
    }
    result = compare_frames(
        pd.DataFrame([{**shared, "revenue": reference}]),
        pd.DataFrame([{**shared, "revenue": 525.2, "is_actual": True}]),
        as_of=CUTOFF,
    )
    actual = result[result.source == "summary"].iloc[0]
    assert actual.status == "unmatched"
    assert pd.isna(actual.spread_pct)
    assert pd.isna(actual.flagged)


def test_comparison_excludes_future_nonactual_and_unit_mismatches():
    shared = {
        "ticker": "KO", "period_start": "2023-01-01", "period_end": "2023-12-31",
        "fiscal_period": "FY", "currency": "USD", "revenue_basis": "period",
    }
    edgar = pd.DataFrame([{**shared, "revenue": 520.0, "revenue_unit": "USD", "available_at": "2024-02-01T12:00:00Z"}])
    summary = pd.DataFrame([
        {**shared, "revenue": 525.0, "revenue_unit": "USD", "available_at": "2024-02-17T12:00:00Z", "is_actual": True},
        {**shared, "revenue": 525.0, "revenue_unit": "USD", "available_at": "2024-02-01T12:00:00Z", "is_actual": False},
        {**shared, "revenue": 525.0, "revenue_unit": "EUR", "available_at": "2024-02-01T12:00:00Z", "is_actual": True},
    ])
    compared = compare_frames(edgar, summary, as_of=CUTOFF)
    vendor = compared[compared.source == "summary"].iloc[0]
    assert vendor.unit == "EUR"
    assert vendor.status == "unmatched"


def test_no_price_basis_means_no_valuation():
    frame, _, _ = normalize()
    class Unverified:
        name = "fixture"
        def quote(self, ticker, filing_date, *, as_of):
            return PriceQuote(20, filing_date, utc("2024-02-15T21:00:00Z"), "USD")
    enriched = enrich_prices(frame, Unverified())
    assert enriched.market_cap.isna().all()
    assert enriched.pe.isna().all()


def test_debt_components_include_short_term_borrowing():
    payload = deepcopy(FIXTURE["companies"]["KO"])
    for tag, value in (("LongTermDebtCurrent", 5), ("LongTermDebtNoncurrent", 30), ("ShortTermBorrowings", 7)):
        payload["facts"]["us-gaap"][tag] = {"units": {"USD": [{"end": "2023-12-31", "val": value, "filed": "2024-02-15", "accn": "fy", "form": "10-K", "fy": 2023, "fp": "FY"}]}}
    frame, _, _ = normalize(payload=payload)
    assert row(frame).total_debt == 42
    assert row(frame).debt_to_equity == pytest.approx(42 / 140)
    assert "ShortTermBorrowings" in row(frame).total_debt_tag


def test_rate_spacing_and_secret_free_cache_identity(tmp_path, monkeypatch):
    clock = [100.0]
    requests_at = []
    class Reply:
        status_code = 200
        def json(self):
            return {"value": 1}
    client = JsonClient(tmp_path)
    monkeypatch.setattr("backtest.fundamentals.http.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("backtest.fundamentals.http.time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(client.session, "get", lambda *a, **kw: requests_at.append(clock[0]) or Reply())
    client.get("https://fixture.local/a", params={"apikey": "DO_NOT_STORE"})
    client.get("https://fixture.local/b", params={"token": "DO_NOT_STORE"})
    assert requests_at[1] - requests_at[0] >= 0.099999
    assert len(client.snapshots("https://fixture.local/a{}")) == 1
    assert all("DO_NOT_STORE" not in p.read_text() for p in tmp_path.rglob("*.json"))


def test_original_eligible_filing_retains_fiscal_calendar_evidence():
    first = {"start": "2023-01-01", "end": "2023-03-31", "val": 100, "filed": "2023-05-01", "accn": "q1", "form": "10-Q", "fy": 2023, "fp": "Q1"}
    repeated = {**first, "filed": "2023-08-01", "accn": "q2", "fp": "Q2"}
    second = {**repeated, "start": "2023-04-01", "end": "2023-06-30", "val": 120}
    payload = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [first, repeated, second]}}}}}
    frame, _, _ = normalize(payload=payload, cutoff="2023-08-02T00:00:00Z")
    assert frame.fiscal_period.tolist() == ["Q1", "Q2"]
    assert frame.revenue.tolist() == [100, 120]
