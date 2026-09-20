import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.fundamentals.__main__ import main
from backtest.fundamentals.api import validate_snapshot
from backtest.fundamentals.core import utc
from backtest.fundamentals.edgar import EdgarSource, normalize_edgar
from backtest.fundamentals.http import JsonClient, Response
from backtest.fundamentals.prices import YFinancePrices

FIXTURES = Path(__file__).parent / "fixtures"
EDGAR = json.loads((FIXTURES / "edgar.json").read_text())
OBSERVED = utc("2024-02-16T10:00:00Z")
AS_OF = utc("2024-02-16T12:00:00Z")


def test_edgar_fetch_reads_historical_submission_pages(monkeypatch):
    calls = []
    class Client:
        def get(self, url):
            calls.append(url)
            if "company_tickers" in url:
                data = {"0": {"ticker": "KO", "cik_str": 21344}}
            elif "companyfacts" in url:
                data = EDGAR["companies"]["KO"]
            elif "submissions-001" in url:
                filings = EDGAR["filings"]
                data = {"accessionNumber": list(filings), "acceptanceDateTime": [f["acceptanceDateTime"] for f in filings.values()], "reportDate": [f["reportDate"] for f in filings.values()]}
            else:
                data = {"filings": {"recent": {}, "files": [{"name": "CIK0000021344-submissions-001.json"}]}}
            return Response(data, OBSERVED)
    source = EdgarSource(as_of=AS_OF, client=Client())
    frame = source.fetch("KO")
    assert len(calls) == 4
    assert "CIK0000021344.json" in calls[1]
    assert not frame.empty
    assert frame.available_at.max() == utc("2024-02-15T21:30:00Z")


def test_edgar_fetch_accepts_historical_cik_map():
    calls = []
    class Client:
        def get(self, url):
            calls.append(url)
            if "companyfacts" in url:
                return Response(EDGAR["companies"]["KO"], OBSERVED)
            return Response({"filings": {"recent": {}}}, OBSERVED)
    EdgarSource(as_of=AS_OF, client=Client(), cik_map={"OLD": "0000021344"}).fetch("OLD")
    assert not any("company_tickers" in url for url in calls)
    assert any("CIK0000021344.json" in url for url in calls)


def test_q4_from_three_standalone_quarters():
    company = json.loads(json.dumps(EDGAR["companies"]["KO"]))
    tags = company["facts"]["us-gaap"]
    revenue = tags["Revenues"]["units"]["USD"]
    revenue[2].update(start="2023-04-01", val=120)
    revenue[3].update(start="2023-07-01", val=140)
    company["facts"]["us-gaap"] = {"Revenues": tags["Revenues"]}
    frame, _, _ = normalize_edgar(company, EDGAR["filings"], "KO", as_of=AS_OF)
    fourth = frame[frame.fiscal_period == "Q4"].iloc[0]
    assert fourth.revenue == 160
    assert fourth.revenue_derived


def test_cli_writes_independent_cutoffs_and_separate_audits(tmp_path, monkeypatch):
    class Source:
        name = "edgar"
        def __init__(self, *, as_of, **kwargs):
            self.as_of = as_of
        def fetch(self, ticker):
            frame, self.superseded, self.archive = normalize_edgar(EDGAR["companies"][ticker], EDGAR["filings"], ticker, as_of=self.as_of)
            return frame
    monkeypatch.setattr("backtest.fundamentals.__main__.EdgarSource", Source)
    code = main(["KO", "--as-of", "2024-01-31T21:00:00Z", "--as-of", "2024-02-16T12:00:00Z", "--sources", "edgar", "--offline", "--output", str(tmp_path)])
    assert code == 0
    data = pd.read_parquet(tmp_path / "fundamentals.parquet")
    assert data.as_of.nunique() == 2
    assert not data.duplicated(["ticker", "period_end", "fiscal_period", "as_of"]).any()
    early = data[data.as_of == utc("2024-01-31T21:00:00Z")]
    assert not early.period_end.eq(pd.Timestamp("2023-12-31")).any()
    assert (tmp_path / "archive.parquet").exists()
    assert (tmp_path / "snapshots.parquet").exists()


def test_cli_builds_each_quarterly_cutoff(tmp_path, monkeypatch):
    class Source:
        name = "edgar"
        def __init__(self, *, as_of, **kwargs):
            self.as_of = as_of
        def fetch(self, ticker):
            return normalize_edgar(EDGAR["companies"][ticker], EDGAR["filings"], ticker, as_of=self.as_of)[0]
    monkeypatch.setattr("backtest.fundamentals.__main__.EdgarSource", Source)
    assert main(["KO", "--quarterly", "2023-06-01", "2023-12-31", "--sources", "edgar", "--offline", "--output", str(tmp_path)]) == 0
    assert pd.read_parquet(tmp_path / "fundamentals.parquet").as_of.nunique() == 3


def test_yahoo_future_snapshots_and_splits_cannot_change_old_quote(tmp_path, monkeypatch):
    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", lambda _: object())
    client = JsonClient(tmp_path, offline=True)
    old = Response({"currency": "USD", "rows": [{"date": "2024-02-15", "close": 20, "split": 0}]}, OBSERVED)
    future = Response({"currency": "USD", "rows": [{"date": "2024-02-15", "close": 10, "split": 0}, {"date": "2025-01-01", "close": 10, "split": 2}]}, utc("2025-02-01T00:00:00Z"))
    monkeypatch.setattr(client, "snapshots", lambda _: [old])
    prices = YFinancePrices(client)
    first = prices.quote("KO", pd.Timestamp("2024-02-15"), as_of=AS_OF)
    monkeypatch.setattr(client, "snapshots", lambda _: [old, future])
    assert prices.quote("KO", pd.Timestamp("2024-02-15"), as_of=AS_OF) == first
    assert first.close == 20


def test_same_day_close_is_not_available_intraday(tmp_path, monkeypatch):
    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", lambda _: object())
    client = JsonClient(tmp_path, offline=True)
    cached = Response({"currency": "USD", "rows": [{"date": "2024-02-15", "close": 20, "split": 0}]}, utc("2024-02-15T18:00:00Z"))
    monkeypatch.setattr(client, "snapshots", lambda _: [cached])
    assert YFinancePrices(client).quote("KO", pd.Timestamp("2024-02-15"), as_of=utc("2024-02-15T19:00:00Z")) is None
