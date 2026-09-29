from fastapi import APIRouter, Request, status

from app.core.http_errors import to_http_exception
from app.schemas import AuthRequest, AuthResponse
from app.services import auth_service
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(request: Request, payload: AuthRequest) -> AuthResponse:
    try:
        with request.app.state.db.connection() as connection:
            token = auth_service.register(
                connection, payload.email, payload.password, client_ip=_client_ip(request)
            )
    except ServiceError as exc:
        raise to_http_exception(exc)
    return AuthResponse(token=token)


@router.post("/login", response_model=AuthResponse)
def login(request: Request, payload: AuthRequest) -> AuthResponse:
    try:
        with request.app.state.db.connection() as connection:
            token = auth_service.login(
                connection, payload.email, payload.password, client_ip=_client_ip(request)
            )
    except ServiceError as exc:
        raise to_http_exception(exc)
    return AuthResponse(token=token)
