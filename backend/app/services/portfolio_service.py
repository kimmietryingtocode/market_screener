from typing import Any

from psycopg.errors import UniqueViolation

from app.repositories import portfolios_repository
from app.repositories.portfolios_repository import HoldingRow, PortfolioRow
from app.schemas import CreatePortfolioRequest
from app.services.errors import ConflictError, NotFoundError, ValidationError


def validate_weights(payload: CreatePortfolioRequest) -> None:
    total = sum(holding.weight for holding in payload.holdings)
    if not 0.999 <= total <= 1.001:
        raise ValidationError(f"holding weights must sum to 1.0, got {total:.4f}")


def create_portfolio(
    connection: Any, user_id: int, payload: CreatePortfolioRequest
) -> tuple[PortfolioRow, list[HoldingRow]]:
    validate_weights(payload)
    try:
        portfolio = portfolios_repository.insert_portfolio(connection, user_id, payload.name)
        holdings = [
            portfolios_repository.insert_holding(connection, portfolio.id, holding.ticker.upper(), holding.weight)
            for holding in payload.holdings
        ]
    except UniqueViolation as exc:
        raise ConflictError("portfolio name or ticker already exists") from exc
    return portfolio, holdings


def list_portfolios(connection: Any, user_id: int) -> list[PortfolioRow]:
    return portfolios_repository.list_portfolios(connection, user_id)


def update_portfolio(
    connection: Any, user_id: int, portfolio_id: int, payload: CreatePortfolioRequest
) -> tuple[PortfolioRow, list[HoldingRow]]:
    """Full replace: name and the entire holdings set. Not a partial patch."""
    validate_weights(payload)
    if portfolios_repository.get_owned_portfolio(connection, portfolio_id, user_id) is None:
        raise NotFoundError("portfolio not found")

    try:
        portfolios_repository.update_portfolio_name(connection, portfolio_id, payload.name)
        holdings = portfolios_repository.replace_holdings(
            connection, portfolio_id, [(h.ticker.upper(), h.weight) for h in payload.holdings]
        )
    except UniqueViolation as exc:
        raise ConflictError("portfolio name or ticker already exists") from exc

    updated = portfolios_repository.get_owned_portfolio(connection, portfolio_id, user_id)
    return updated, holdings


def delete_portfolio(connection: Any, user_id: int, portfolio_id: int) -> None:
    if portfolios_repository.get_owned_portfolio(connection, portfolio_id, user_id) is None:
        raise NotFoundError("portfolio not found")
    portfolios_repository.delete_portfolio(connection, portfolio_id)
