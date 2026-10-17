import hashlib
import json
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.redis_client import get_client
from app.repositories import analytics_repository, portfolios_repository
from app.services.errors import NotFoundError, UpstreamServiceError, ValidationError

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300


DEFAULT_LOOKBACK = "1y"


def _cache_key(portfolio_id: int, holdings_payload: list[dict], lookback: str) -> str:
    """Keyed by the holdings themselves, not just the portfolio id, so
    editing a portfolio's holdings can't serve the previous mix's numbers."""
    digest = hashlib.sha256(json.dumps(holdings_payload, sort_keys=True).encode()).hexdigest()[:16]
    return f"analytics:portfolio:{portfolio_id}:{lookback}:{digest}"


def get_portfolio_analytics(
    connection: Any, portfolio_id: int, user_id: int, lookback: str = DEFAULT_LOOKBACK
) -> dict:
    holdings = portfolios_repository.get_holdings_for_owned_portfolio(connection, portfolio_id, user_id)
    if not holdings:
        raise NotFoundError("portfolio not found or has no holdings")

    holdings_payload = [{"ticker": h.ticker, "weight": float(h.weight)} for h in holdings]
    cache_key = _cache_key(portfolio_id, holdings_payload, lookback)
    client = get_client()

    if client is not None:
        try:
            cached = client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.warning("analytics cache read failed for %s", cache_key, exc_info=True)

    try:
        response = httpx.post(
            f"{settings.quant_service_url}/analytics/portfolio",
            json={"holdings": holdings_payload, "lookback": lookback},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise UpstreamServiceError("quant service unreachable") from exc

    # A 422 means the request itself cannot be analysed (unknown ticker, not
    # enough price history). That is the caller's problem, so surface it as
    # such instead of blaming the dependency with a 502.
    if response.status_code == 422:
        detail = "portfolio cannot be analysed"
        try:
            detail = response.json().get("detail", detail)
        except Exception:
            pass
        raise ValidationError(str(detail))

    if response.status_code != 200:
        raise UpstreamServiceError("quant service returned an error")

    result = response.json()
    analytics_repository.insert_run(connection, portfolio_id, result)

    if client is not None:
        try:
            client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(result))
        except Exception:
            logger.warning("analytics cache write failed for %s", cache_key, exc_info=True)

    return result
