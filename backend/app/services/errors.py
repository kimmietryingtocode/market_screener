"""Service-layer errors, translated to HTTP responses by the controllers.

Keeping these independent of FastAPI/HTTPException means the service layer
has no web-framework dependency and stays unit-testable on its own.
"""


class ServiceError(Exception):
    """Base for errors a controller knows how to translate."""


class NotFoundError(ServiceError):
    pass


class ConflictError(ServiceError):
    pass


class ValidationError(ServiceError):
    pass


class UnauthorizedError(ServiceError):
    pass


class UpstreamServiceError(ServiceError):
    pass


class RateLimitedError(ServiceError):
    pass
