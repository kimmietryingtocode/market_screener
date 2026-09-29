"""Shared fixtures for service-layer tests.

These test the service layer directly against a real Postgres instance
(no FastAPI, no HTTP) — the whole point of pulling business logic out of
the routers was to make it testable without spinning up the app. Same
TEST_DATABASE_URL skip-if-unavailable convention as worker/test_*.py.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


def _db_available() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        with psycopg.connect(TEST_DATABASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()

requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="TEST_DATABASE_URL not set or unreachable")


@pytest.fixture
def connection():
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture
def make_user(connection):
    """Factory fixture: make_user() -> user_id, creating a throwaway user
    per call and deleting every one of them (cascades to their portfolios/
    holdings/backtests) at teardown."""
    created_ids: list[int] = []

    def _make() -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, 'unused') RETURNING id",
                (f"svc-test-{uuid.uuid4().hex}@example.com",),
            )
            user_id = cursor.fetchone()[0]
        created_ids.append(user_id)
        return user_id

    yield _make

    with connection.cursor() as cursor:
        for user_id in created_ids:
            # backtests.user_id/portfolio_id are ON DELETE SET NULL, not
            # CASCADE, so a backtest a test created would otherwise survive
            # the user delete as an orphaned row.
            cursor.execute("DELETE FROM backtests WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
