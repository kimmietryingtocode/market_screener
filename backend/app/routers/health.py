"""Liveness and readiness probes.

These are deliberately two different things:

/healthz  — liveness. "This process is up." Cheap, no dependencies. An
            orchestrator restarts the container when this fails, so it must
            not fail just because Postgres is briefly unreachable — that
            would turn a database blip into a restart storm.
/readyz   — readiness. "This instance can serve traffic." Checks the
            dependencies it cannot serve a request without. Failing this
            pulls the instance out of the load balancer without killing it.
"""

from fastapi import APIRouter, Request, Response, status

from app.core.redis_client import get_client

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        with request.app.state.db.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {type(exc).__name__}"

    # Redis is optional by design (the cache and rate limiter both fail
    # open), so a Redis problem is reported but does not make the instance
    # unready — it would only degrade performance.
    client = get_client()
    if client is None:
        checks["redis"] = "not configured"
    else:
        try:
            client.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"degraded: {type(exc).__name__}"

    ready = checks["postgres"] == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"ready": ready, "checks": checks}
