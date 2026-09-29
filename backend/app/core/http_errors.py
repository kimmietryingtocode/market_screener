"""Maps service-layer errors to HTTP responses, so controllers stay thin."""

from fastapi import HTTPException, status

from app.services.errors import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    UpstreamServiceError,
    ValidationError,
)

_STATUS_BY_ERROR = {
    ValidationError: status.HTTP_400_BAD_REQUEST,
    UnauthorizedError: status.HTTP_401_UNAUTHORIZED,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    UpstreamServiceError: status.HTTP_502_BAD_GATEWAY,
}


def to_http_exception(exc: Exception) -> HTTPException:
    for error_type, http_status in _STATUS_BY_ERROR.items():
        if isinstance(exc, error_type):
            return HTTPException(status_code=http_status, detail=str(exc))
    raise exc
