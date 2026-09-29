"""Health/readiness probe and middleware tests.

Uses TestClient with a stub connection pool on app.state.db, so these run
without Postgres — the point is the probe's branching, not the database.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.core.middleware import REQUEST_ID_HEADER
from app.main import app


class StubCursor:
    def __init__(self, fail: bool):
        self.fail = fail

    def execute(self, *args, **kwargs):
        if self.fail:
            raise ConnectionError("postgres unreachable")

    def fetchone(self):
        return (1,)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


class StubPool:
    def __init__(self, fail: bool = False):
        self.fail = fail

    @contextmanager
    def connection(self):
        yield self

    def cursor(self):
        return StubCursor(self.fail)


@pytest.fixture
def client_with_db():
    app.state.db = StubPool()
    # raise_server_exceptions=False so the app's own 500 handler runs,
    # instead of TestClient re-raising into the test.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_with_broken_db():
    app.state.db = StubPool(fail=True)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def no_redis(monkeypatch):
    monkeypatch.setattr("app.routers.health.get_client", lambda: None)


def test_healthz_is_cheap_and_does_not_touch_the_database(client_with_broken_db):
    """Liveness must stay green while Postgres is down, or a DB blip turns
    into a container restart loop."""
    response = client_with_broken_db.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_ready_when_the_database_answers(client_with_db):
    response = client_with_db.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["postgres"] == "ok"


def test_readyz_returns_503_when_the_database_is_unreachable(client_with_broken_db):
    """This is the whole point: the old /healthz said ok regardless, so a
    load balancer kept sending traffic to an instance that could not serve it."""
    response = client_with_broken_db.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["checks"]["postgres"].startswith("error:")


def test_readyz_stays_ready_when_redis_is_absent(client_with_db):
    """Redis is optional — cache and rate limiter both fail open — so its
    absence must not pull the instance out of rotation."""
    response = client_with_db.get("/readyz")

    assert response.status_code == 200
    assert response.json()["checks"]["redis"] == "not configured"


def test_readyz_marks_redis_degraded_without_becoming_unready(client_with_db, monkeypatch):
    class BrokenRedis:
        def ping(self):
            raise ConnectionError("redis down")

    monkeypatch.setattr("app.routers.health.get_client", lambda: BrokenRedis())

    response = client_with_db.get("/readyz")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["checks"]["redis"].startswith("degraded:")


def test_every_response_carries_a_request_id(client_with_db):
    response = client_with_db.get("/healthz")
    assert response.headers[REQUEST_ID_HEADER]


def test_an_upstream_request_id_is_preserved_for_tracing(client_with_db):
    response = client_with_db.get("/healthz", headers={REQUEST_ID_HEADER: "trace-me-123"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-me-123"


def test_unhandled_errors_return_an_opaque_500_with_the_request_id(client_with_db):
    @app.get("/_boom_for_tests")
    def boom():
        raise RuntimeError("database password is hunter2")

    response = client_with_db.get("/_boom_for_tests")

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "internal server error"
    assert body["request_id"]
    # The exception text must never reach the client.
    assert "hunter2" not in response.text
