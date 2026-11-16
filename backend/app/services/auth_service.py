import secrets
from typing import Any

from psycopg.errors import UniqueViolation

from app.core import rate_limit
from app.core.google_auth import verify_google_id_token
from app.core.security import create_access_token, hash_password, verify_password
from app.repositories import users_repository
from app.services.errors import ConflictError, RateLimitedError, UnauthorizedError


def register(connection: Any, email: str, password: str, *, client_ip: str | None = None) -> str:
    if client_ip and not rate_limit.check_register(client_ip):
        raise RateLimitedError("too many registration attempts; try again later")

    normalized_email = email.lower()
    try:
        user_id = users_repository.insert_user(connection, normalized_email, hash_password(password))
    except UniqueViolation as exc:
        raise ConflictError("email already registered") from exc
    return create_access_token(user_id)


def login(connection: Any, email: str, password: str, *, client_ip: str | None = None) -> str:
    if client_ip and not rate_limit.check_login(client_ip):
        raise RateLimitedError("too many login attempts; try again later")

    user = users_repository.find_by_email(connection, email.lower())
    if user is None or not verify_password(password, user[1]):
        raise UnauthorizedError("invalid email or password")
    user_id, _password_hash = user
    return create_access_token(user_id)


def google_login(
    connection: Any,
    credential: str,
    *,
    client_ip: str | None = None,
) -> tuple[str, str, str | None]:
    if client_ip and not rate_limit.check_login(client_ip):
        raise RateLimitedError("too many login attempts; try again later")

    claims = verify_google_id_token(credential)
    google_sub = str(claims["sub"])
    email = str(claims["email"]).lower()
    display_name = str(claims["name"]) if claims.get("name") else None

    existing_google_user = users_repository.find_by_google_sub(connection, google_sub)
    if existing_google_user:
        user_id, stored_email = existing_google_user
        return create_access_token(user_id), stored_email, display_name

    # Password accounts require an explicit linking flow before Google can own them.
    if users_repository.find_by_email(connection, email):
        raise ConflictError("email already registered; sign in with your password")

    try:
        password_hash = hash_password(secrets.token_urlsafe(48))
        user_id = users_repository.insert_google_user(
            connection,
            email,
            password_hash,
            google_sub,
        )
    except UniqueViolation as exc:
        raise ConflictError("Google account is already registered") from exc

    return create_access_token(user_id), email, display_name
