from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_quotes_defaults_to_the_broad_market_list_when_no_tickers_given(monkeypatch):
    captured = {}

    def fake_fetch(tickers):
        captured["tickers"] = tickers
        return [{"ticker": t, "price": 1.0, "previous_close": None, "change": None, "change_percent": None,
                  "open": None, "day_high": None, "day_low": None, "fifty_two_week_high": None,
                  "fifty_two_week_low": None, "volume": None, "market_cap": None, "currency": None,
                  "exchange": None, "quote_type": None} for t in tickers]

    monkeypatch.setattr("app.routers.market.fetch_market_quotes", fake_fetch)

    response = client.get("/market/quotes")

    assert response.status_code == 200
    assert captured["tickers"] == response.json()["tickers"]
    assert len(captured["tickers"]) > 0


def test_quotes_rejects_too_many_tickers_with_422(monkeypatch):
    def fail_if_called(tickers):
        raise AssertionError("should not reach the fetcher")

    monkeypatch.setattr("app.routers.market.fetch_market_quotes", fail_if_called)

    too_many = ",".join(f"T{i}" for i in range(30))
    response = client.get(f"/market/quotes?tickers={too_many}")

    assert response.status_code == 422


def test_quotes_provider_outage_surfaces_as_503_not_500(monkeypatch):
    def raise_runtime_error(tickers):
        raise RuntimeError("no quote data returned for AAPL")

    monkeypatch.setattr("app.routers.market.fetch_market_quotes", raise_runtime_error)

    response = client.get("/market/quotes?tickers=AAPL")

    assert response.status_code == 503


def test_history_returns_bars_keyed_by_ticker(monkeypatch):
    fake_bars = {"AAPL": [{"timestamp": "2025-01-02T00:00:00", "open": 1, "high": 2, "low": 1,
                            "close": 1.5, "adjusted_close": 1.5, "volume": 100}]}
    monkeypatch.setattr("app.routers.market.fetch_price_bars", lambda tickers, period, interval: fake_bars)

    response = client.get("/market/history?tickers=AAPL&period=1mo&interval=1d")

    assert response.status_code == 200
    body = response.json()
    assert body["period"] == "1mo"
    assert body["bars"] == fake_bars
