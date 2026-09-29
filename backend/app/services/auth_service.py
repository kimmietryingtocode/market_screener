from typing import Any

from psycopg.errors import UniqueViolation

from app.core import rate_limit
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
