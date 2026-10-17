from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID_PAYLOAD = {"holdings": [{"ticker": "AAPL", "weight": 0.6}, {"ticker": "MSFT", "weight": 0.4}], "lookback": "1y"}


def test_valid_request_returns_the_computed_metrics(monkeypatch):
    fake_result = {"annualized_return": 0.1, "volatility": 0.2, "sharpe_ratio": 0.5, "max_drawdown": -0.15, "lookback": "1y"}
    monkeypatch.setattr(
        "app.routers.analytics.compute_portfolio_analytics",
        lambda tickers, weights, lookback: fake_result,
    )

    response = client.post("/analytics/portfolio", json=VALID_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    # Assert the fields under test rather than the whole shape, so adding an
    # optional field to the response model doesn't fail this.
    for key, value in fake_result.items():
        assert body[key] == value


def test_weights_not_summing_to_one_is_rejected_before_any_computation(monkeypatch):
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("app.routers.analytics.compute_portfolio_analytics", fail_if_called)

    payload = {"holdings": [{"ticker": "AAPL", "weight": 0.9}, {"ticker": "MSFT", "weight": 0.9}], "lookback": "1y"}
    response = client.post("/analytics/portfolio", json=payload)

    assert response.status_code == 422
    assert called is False


def test_bad_ticker_or_lookback_surfaces_as_422_not_500(monkeypatch):
    def raise_value_error(tickers, weights, lookback):
        raise ValueError("no price data returned for given tickers/lookback")

    monkeypatch.setattr("app.routers.analytics.compute_portfolio_analytics", raise_value_error)

    response = client.post("/analytics/portfolio", json=VALID_PAYLOAD)

    assert response.status_code == 422
    assert "no price data" in response.json()["detail"]
