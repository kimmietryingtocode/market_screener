"""Fixed-window rate limiting backed by Redis.

Used on the auth endpoints, which are the ones worth protecting: without
this, /auth/login is an unmetered password-guessing oracle and
/auth/register lets one client create unlimited accounts.

Fails open — if Redis is unavailable the request proceeds. A login endpoint
that rejects everyone because the cache is down is a worse outage than one
that is briefly unmetered.
"""

from __future__ import annotations

import logging

from app.core.redis_client import get_client

logger = logging.getLogger(__name__)

LOGIN_LIMIT = 10
LOGIN_WINDOW_SECONDS = 60
REGISTER_LIMIT = 5
REGISTER_WINDOW_SECONDS = 300


def hit(key: str, *, limit: int, window_seconds: int) -> bool:
    """Count one request against `key`. Returns False if over the limit.

    INCR then EXPIRE on first hit: the window starts when the first request
    in it arrives and the key expires on its own, so there's nothing to
    clean up.
    """
    client = get_client()
    if client is None:
        return True

    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_seconds)
        return count <= limit
    except Exception:
        logger.warning("rate limit check failed for %s; allowing the request", key, exc_info=True)
        return True


def check_login(client_ip: str) -> bool:
    return hit(f"ratelimit:login:{client_ip}", limit=LOGIN_LIMIT, window_seconds=LOGIN_WINDOW_SECONDS)


def check_register(client_ip: str) -> bool:
    return hit(f"ratelimit:register:{client_ip}", limit=REGISTER_LIMIT, window_seconds=REGISTER_WINDOW_SECONDS)
