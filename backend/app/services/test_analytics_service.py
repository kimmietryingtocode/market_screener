import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.schemas import CreatePortfolioRequest, HoldingInput
from app.services import analytics_service, portfolio_service
from app.services.conftest import requires_db
from app.services.errors import NotFoundError, UpstreamServiceError, ValidationError

QUANT_URL = f"{settings.quant_service_url}/analytics/portfolio"

FAKE_RESULT = {
    "annualized_return": 0.12,
    "volatility": 0.2,
    "sharpe_ratio": 0.6,
    "max_drawdown": -0.1,
    "lookback": "1y",
}


class FakeRedis:
    """Minimal get/setex used by the analytics cache."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(analytics_service, "get_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def no_redis_by_default(monkeypatch, request):
    """Most tests here are about the upstream/DB path, so default to Redis
    being absent unless a test asks for the fake."""
    if "fake_redis" in request.fixturenames:
        return
    monkeypatch.setattr(analytics_service, "get_client", lambda: None)


@requires_db
def test_returns_upstream_result_and_persists_the_run(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )
    fake_result = {"annualized_return": 0.12, "volatility": 0.2, "sharpe_ratio": 0.6, "max_drawdown": -0.1, "lookback": "1y"}

    with respx.mock:
        respx.post(QUANT_URL).mock(return_value=httpx.Response(200, json=fake_result))
        result = analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)

    assert result == fake_result
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM analytics_runs WHERE portfolio_id = %s", (portfolio.id,))
        assert cursor.fetchone()[0] == 1


@requires_db
def test_portfolio_with_no_holdings_or_not_owned_raises_not_found(connection, make_user):
    user_id = make_user()
    with pytest.raises(NotFoundError):
        analytics_service.get_portfolio_analytics(connection, 999_999_999, user_id)


@requires_db
def test_unanalysable_portfolio_is_a_validation_error_not_a_bad_gateway(connection, make_user):
    """A 422 from quant means "this portfolio can't be analysed" (unknown
    ticker, too little history). Reporting that as 502 would tell the user
    our dependency is broken when the request is the problem."""
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with respx.mock:
        respx.post(QUANT_URL).mock(
            return_value=httpx.Response(422, json={"detail": "insufficient price history: 2 daily returns"})
        )
        with pytest.raises(ValidationError, match="insufficient price history"):
            analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)


@requires_db
def test_lookback_is_part_of_the_cache_key(connection, make_user, fake_redis):
    """Two lookbacks are two different questions; one must not serve the
    other's cached answer."""
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with respx.mock:
        route = respx.post(QUANT_URL).mock(return_value=httpx.Response(200, json=FAKE_RESULT))
        analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id, "1y")
        analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id, "3mo")

        assert route.call_count == 2  # not served from the 1y entry

    assert len(fake_redis.store) == 2


@requires_db
def test_requested_lookback_is_forwarded_to_the_quant_service(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with respx.mock:
        route = respx.post(QUANT_URL).mock(return_value=httpx.Response(200, json=FAKE_RESULT))
        analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id, "6mo")

        assert json.loads(route.calls[0].request.content)["lookback"] == "6mo"


@requires_db
def test_upstream_non_200_raises_upstream_service_error(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with respx.mock:
        # 5xx is a genuine dependency failure, unlike 422 which means the
        # request itself was unanalysable.
        respx.post(QUANT_URL).mock(return_value=httpx.Response(503, json={"detail": "provider down"}))
        with pytest.raises(UpstreamServiceError):
            analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)


@requires_db
def test_upstream_unreachable_raises_upstream_service_error(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with respx.mock:
        respx.post(QUANT_URL).mock(side_effect=httpx.ConnectError("connection refused"))
        with pytest.raises(UpstreamServiceError):
            analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)


@requires_db
def test_second_call_is_served_from_cache_without_hitting_the_quant_service(connection, make_user, fake_redis):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with respx.mock:
        route = respx.post(QUANT_URL).mock(return_value=httpx.Response(200, json=FAKE_RESULT))
        first = analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)
        second = analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)

        assert route.call_count == 1  # second call never left the process

    assert first == second == FAKE_RESULT
    # The cache is a hot layer only — Postgres still recorded just the one real run.
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM analytics_runs WHERE portfolio_id = %s", (portfolio.id,))
        assert cursor.fetchone()[0] == 1


@requires_db
def test_changing_holdings_invalidates_the_cached_result(connection, make_user, fake_redis):
    """The cache key includes the holdings, so an edited portfolio must not
    keep serving the old mix's numbers."""
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )
    revised_result = {**FAKE_RESULT, "annualized_return": 0.99}

    with respx.mock:
        respx.post(QUANT_URL).mock(return_value=httpx.Response(200, json=FAKE_RESULT))
        analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)

    portfolio_service.update_portfolio(
        connection, user_id, portfolio.id,
        CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="MSFT", weight=1.0)]),
    )

    with respx.mock:
        route = respx.post(QUANT_URL).mock(return_value=httpx.Response(200, json=revised_result))
        result = analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)
        assert route.call_count == 1  # recomputed, not served from the stale entry

    assert result["annualized_return"] == 0.99


@requires_db
def test_a_broken_cache_does_not_break_the_request(connection, make_user, monkeypatch):
    class BrokenRedis:
        def get(self, key):
            raise ConnectionError("redis down")

        def setex(self, key, ttl, value):
            raise ConnectionError("redis down")

    monkeypatch.setattr(analytics_service, "get_client", lambda: BrokenRedis())
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with respx.mock:
        respx.post(QUANT_URL).mock(return_value=httpx.Response(200, json=FAKE_RESULT))
        result = analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)

    assert result == FAKE_RESULT


@requires_db
def test_cached_payload_is_json_serializable_as_stored(connection, make_user, fake_redis):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="P1", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with respx.mock:
        respx.post(QUANT_URL).mock(return_value=httpx.Response(200, json=FAKE_RESULT))
        analytics_service.get_portfolio_analytics(connection, portfolio.id, user_id)

    stored = list(fake_redis.store.values())
    assert len(stored) == 1
    assert json.loads(stored[0]) == FAKE_RESULT
