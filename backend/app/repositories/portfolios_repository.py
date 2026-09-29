"""Raw SQL for `portfolios` and `holdings`. No weight validation or ownership
policy here — that belongs to app.services.portfolio_service."""

from datetime import datetime
from typing import Any, NamedTuple


class PortfolioRow(NamedTuple):
    id: int
    name: str
    created_at: datetime


class HoldingRow(NamedTuple):
    id: int
    portfolio_id: int
    ticker: str
    weight: float


def insert_portfolio(connection: Any, user_id: int, name: str) -> PortfolioRow:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO portfolios (user_id, name) VALUES (%s, %s) RETURNING id, name, created_at",
            (user_id, name),
        )
        return PortfolioRow(*cursor.fetchone())


def insert_holding(connection: Any, portfolio_id: int, ticker: str, weight: float) -> HoldingRow:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO holdings (portfolio_id, ticker, weight) VALUES (%s, %s, %s) RETURNING id",
            (portfolio_id, ticker, weight),
        )
        holding_id = cursor.fetchone()[0]
        return HoldingRow(id=holding_id, portfolio_id=portfolio_id, ticker=ticker, weight=weight)


def list_portfolios(connection: Any, user_id: int) -> list[PortfolioRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, name, created_at FROM portfolios WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return [PortfolioRow(*row) for row in cursor.fetchall()]


def exists_for_user(connection: Any, portfolio_id: int, user_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM portfolios WHERE id = %s AND user_id = %s",
            (portfolio_id, user_id),
        )
        return cursor.fetchone() is not None


def get_owned_portfolio(connection: Any, portfolio_id: int, user_id: int) -> PortfolioRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, name, created_at FROM portfolios WHERE id = %s AND user_id = %s",
            (portfolio_id, user_id),
        )
        row = cursor.fetchone()
        return PortfolioRow(*row) if row else None


def update_portfolio_name(connection: Any, portfolio_id: int, name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("UPDATE portfolios SET name = %s WHERE id = %s", (name, portfolio_id))


def replace_holdings(connection: Any, portfolio_id: int, holdings: list[tuple[str, float]]) -> list[HoldingRow]:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
        result = []
        for ticker, weight in holdings:
            cursor.execute(
                "INSERT INTO holdings (portfolio_id, ticker, weight) VALUES (%s, %s, %s) RETURNING id",
                (portfolio_id, ticker, weight),
            )
            result.append(HoldingRow(id=cursor.fetchone()[0], portfolio_id=portfolio_id, ticker=ticker, weight=weight))
        return result


def delete_portfolio(connection: Any, portfolio_id: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM portfolios WHERE id = %s", (portfolio_id,))


def get_holdings_for_owned_portfolio(connection: Any, portfolio_id: int, user_id: int) -> list[HoldingRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT id, portfolio_id, ticker, weight FROM holdings
               WHERE portfolio_id = %s
               AND portfolio_id IN (SELECT id FROM portfolios WHERE user_id = %s)""",
            (portfolio_id, user_id),
        )
        # weight is NUMERIC in Postgres -> psycopg returns Decimal; cast so
        # this actually matches HoldingRow's declared float type.
        return [
            HoldingRow(id=row[0], portfolio_id=row[1], ticker=row[2], weight=float(row[3]))
            for row in cursor.fetchall()
        ]
