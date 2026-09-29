"""Rate limiter tests using a fake Redis, so they need no running server."""

from __future__ import annotations

import pytest

from app.core import rate_limit


class FakeRedis:
    def __init__(self, *, fail: bool = False):
        self.counters: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.fail = fail

    def incr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key: str, seconds: int) -> None:
        self.expiries[key] = seconds


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "get_client", lambda: fake)
    return fake


def test_allows_requests_under_the_limit(fake_redis):
    for _ in range(3):
        assert rate_limit.hit("k", limit=3, window_seconds=60) is True


def test_blocks_the_request_that_exceeds_the_limit(fake_redis):
    for _ in range(3):
        rate_limit.hit("k", limit=3, window_seconds=60)
    assert rate_limit.hit("k", limit=3, window_seconds=60) is False


def test_sets_the_window_expiry_only_on_the_first_hit(fake_redis):
    rate_limit.hit("k", limit=5, window_seconds=42)
    rate_limit.hit("k", limit=5, window_seconds=42)
    assert fake_redis.expiries == {"k": 42}


def test_separate_keys_do_not_share_a_budget(fake_redis):
    for _ in range(3):
        rate_limit.hit("ip-a", limit=3, window_seconds=60)
    assert rate_limit.hit("ip-a", limit=3, window_seconds=60) is False
    assert rate_limit.hit("ip-b", limit=3, window_seconds=60) is True


def test_fails_open_when_redis_is_unreachable(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_client", lambda: FakeRedis(fail=True))
    # A broken cache must not lock every user out of logging in.
    assert rate_limit.hit("k", limit=1, window_seconds=60) is True


def test_fails_open_when_redis_is_not_configured(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_client", lambda: None)
    assert rate_limit.hit("k", limit=1, window_seconds=60) is True


def test_login_and_register_use_separate_budgets(fake_redis):
    rate_limit.check_login("1.2.3.4")
    rate_limit.check_register("1.2.3.4")
    assert set(fake_redis.counters) == {"ratelimit:login:1.2.3.4", "ratelimit:register:1.2.3.4"}
