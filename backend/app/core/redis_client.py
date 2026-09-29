"""Optional Redis connection, shared by the analytics cache and the auth
rate limiter.

Redis is deliberately optional: if REDIS_URL isn't configured, or Redis is
down, every caller here degrades to "no cache / no rate limit" rather than
failing the request. Postgres remains the source of truth for analytics
results (see the comment on analytics_runs in 001_init.sql), so a cache miss
is only ever a performance cost, never a correctness one.
"""

from __future__ import annotations

import logging

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None
_initialized = False


def get_client() -> redis.Redis | None:
    """Return a shared client, or None if Redis isn't configured/reachable."""
    global _client, _initialized
    if _initialized:
        return _client

    _initialized = True
    if not settings.redis_url:
        logger.info("REDIS_URL not set; analytics caching and rate limiting are disabled")
        _client = None
        return None

    try:
        candidate = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
        candidate.ping()
    except Exception:
        logger.warning("Redis at %s is unreachable; continuing without it", settings.redis_url, exc_info=True)
        _client = None
        return None

    _client = candidate
    return _client


def reset_for_tests() -> None:
    """Drop the memoized client so a test can swap REDIS_URL or inject a fake."""
    global _client, _initialized
    _client = None
    _initialized = False
