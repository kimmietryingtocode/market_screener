from fastapi import APIRouter, HTTPException, Request, status
from psycopg.errors import UniqueViolation

from app.auth import create_access_token, hash_password, verify_password
from app.schemas import AuthRequest, AuthResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(request: Request, payload: AuthRequest) -> AuthResponse:
    try:
        with request.app.state.db.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                    (payload.email.lower(), hash_password(payload.password)),
                )
                user_id = cursor.fetchone()[0]
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="email already registered") from exc

    return AuthResponse(token=create_access_token(user_id))


@router.post("/login", response_model=AuthResponse)
def login(request: Request, payload: AuthRequest) -> AuthResponse:
    with request.app.state.db.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, password_hash FROM users WHERE email = %s",
                (payload.email.lower(),),
            )
            user = cursor.fetchone()

    if user is None or not verify_password(payload.password, user[1]):
        raise HTTPException(status_code=401, detail="invalid email or password")

    return AuthResponse(token=create_access_token(user[0]))
