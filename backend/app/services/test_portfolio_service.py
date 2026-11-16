import pytest

from app.repositories import portfolios_repository
from app.schemas import CreatePortfolioRequest, HoldingInput
from app.services import portfolio_service
from app.services.conftest import requires_db
from app.services.errors import ConflictError, NotFoundError, ValidationError


@requires_db
def test_create_portfolio_with_valid_weights(connection, make_user):
    user_id = make_user()
    payload = CreatePortfolioRequest(
        name="Balanced",
        holdings=[HoldingInput(ticker="aapl", weight=0.6), HoldingInput(ticker="jpm", weight=0.4)],
    )

    portfolio, holdings = portfolio_service.create_portfolio(connection, user_id, payload)

    assert portfolio.name == "Balanced"
    assert {h.ticker for h in holdings} == {"AAPL", "JPM"}  # uppercased


@requires_db
def test_create_portfolio_rejects_weights_not_summing_to_one(connection, make_user):
    user_id = make_user()
    payload = CreatePortfolioRequest(
        name="Bad Weights",
        holdings=[HoldingInput(ticker="AAPL", weight=0.5), HoldingInput(ticker="JPM", weight=0.2)],
    )

    with pytest.raises(ValidationError):
        portfolio_service.create_portfolio(connection, user_id, payload)


@requires_db
def test_create_portfolio_duplicate_name_for_same_user_raises_conflict(connection, make_user):
    user_id = make_user()
    payload = CreatePortfolioRequest(name="Dup", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    portfolio_service.create_portfolio(connection, user_id, payload)

    with pytest.raises(ConflictError):
        portfolio_service.create_portfolio(connection, user_id, payload)


@requires_db
def test_list_portfolios_only_returns_the_calling_users_portfolios(connection, make_user):
    user_a = make_user()
    user_b = make_user()
    portfolio_service.create_portfolio(
        connection, user_a, CreatePortfolioRequest(name="A's Portfolio", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )
    portfolio_service.create_portfolio(
        connection, user_b, CreatePortfolioRequest(name="B's Portfolio", holdings=[HoldingInput(ticker="MSFT", weight=1.0)])
    )

    a_portfolios = portfolio_service.list_portfolios(connection, user_a)

    assert [p.name for p in a_portfolios] == ["A's Portfolio"]


@requires_db
def test_update_portfolio_replaces_name_and_holdings(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="Original", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    updated, holdings = portfolio_service.update_portfolio(
        connection, user_id, portfolio.id,
        CreatePortfolioRequest(name="Renamed", holdings=[HoldingInput(ticker="MSFT", weight=0.5), HoldingInput(ticker="JPM", weight=0.5)]),
    )

    assert updated.name == "Renamed"
    assert {h.ticker for h in holdings} == {"MSFT", "JPM"}  # AAPL fully replaced, not merged


@requires_db
def test_update_portfolio_rejects_bad_weights_without_touching_existing_data(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="Original", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with pytest.raises(ValidationError):
        portfolio_service.update_portfolio(
            connection, user_id, portfolio.id,
            CreatePortfolioRequest(name="Renamed", holdings=[HoldingInput(ticker="MSFT", weight=0.3)]),
        )

    # rejected before any repository call, so the original holding must survive
    holdings = portfolios_repository.get_holdings_for_owned_portfolio(connection, portfolio.id, user_id)
    assert [h.ticker for h in holdings] == ["AAPL"]


@requires_db
def test_update_portfolio_rolls_back_name_and_holdings_on_duplicate_ticker(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection,
        user_id,
        CreatePortfolioRequest(name="Original", holdings=[HoldingInput(ticker="AAPL", weight=1.0)]),
    )

    with pytest.raises(ConflictError):
        portfolio_service.update_portfolio(
            connection,
            user_id,
            portfolio.id,
            CreatePortfolioRequest(
                name="Must Roll Back",
                holdings=[
                    HoldingInput(ticker="MSFT", weight=0.5),
                    HoldingInput(ticker="msft", weight=0.5),
                ],
            ),
        )

    unchanged = portfolios_repository.get_owned_portfolio(connection, portfolio.id, user_id)
    holdings = portfolios_repository.get_holdings_for_owned_portfolio(
        connection, portfolio.id, user_id
    )
    assert unchanged.name == "Original"
    assert [h.ticker for h in holdings] == ["AAPL"]


@requires_db
def test_update_portfolio_for_another_users_portfolio_raises_not_found(connection, make_user):
    owner_id = make_user()
    attacker_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, owner_id, CreatePortfolioRequest(name="Owner's", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with pytest.raises(NotFoundError):
        portfolio_service.update_portfolio(
            connection, attacker_id, portfolio.id,
            CreatePortfolioRequest(name="Hijacked", holdings=[HoldingInput(ticker="MSFT", weight=1.0)]),
        )


@requires_db
def test_delete_portfolio_removes_it_and_its_holdings(connection, make_user):
    user_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, user_id, CreatePortfolioRequest(name="ToDelete", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    portfolio_service.delete_portfolio(connection, user_id, portfolio.id)

    assert portfolio_service.list_portfolios(connection, user_id) == []
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM holdings WHERE portfolio_id = %s", (portfolio.id,))
        assert cursor.fetchone()[0] == 0


@requires_db
def test_delete_portfolio_for_another_users_portfolio_raises_not_found(connection, make_user):
    owner_id = make_user()
    attacker_id = make_user()
    portfolio, _ = portfolio_service.create_portfolio(
        connection, owner_id, CreatePortfolioRequest(name="Owner's", holdings=[HoldingInput(ticker="AAPL", weight=1.0)])
    )

    with pytest.raises(NotFoundError):
        portfolio_service.delete_portfolio(connection, attacker_id, portfolio.id)

    # still there — the delete must not have gone through
    assert portfolio_service.list_portfolios(connection, owner_id) != []
