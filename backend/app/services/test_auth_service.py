import uuid

import jwt
import pytest

from app.core.config import settings
from app.services import auth_service
from app.services.errors import ConflictError, UnauthorizedError
from app.services.conftest import requires_db


def _email() -> str:
    return f"auth-test-{uuid.uuid4().hex}@example.com"


@requires_db
def test_register_returns_a_token_carrying_the_new_user_id(connection):
    email = _email()
    token = auth_service.register(connection, email, "correct-horse-battery")

    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert isinstance(payload["user_id"], int)

    # Clean up the row register() created directly (not via make_user,
    # which only creates rows itself) so it doesn't leak between tests.
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM users WHERE email = %s", (email.lower(),))


@requires_db
def test_register_duplicate_email_raises_conflict_not_a_db_error(connection):
    email = _email()
    auth_service.register(connection, email, "correct-horse-battery")
    try:
        with pytest.raises(ConflictError):
            auth_service.register(connection, email.upper(), "different-password")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE email = %s", (email.lower(),))


@requires_db
def test_login_with_correct_password_succeeds(connection):
    email = _email()
    auth_service.register(connection, email, "correct-horse-battery")
    try:
        token = auth_service.login(connection, email, "correct-horse-battery")
        assert isinstance(token, str) and token
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE email = %s", (email.lower(),))


@requires_db
def test_login_with_wrong_password_raises_unauthorized(connection):
    email = _email()
    auth_service.register(connection, email, "correct-horse-battery")
    try:
        with pytest.raises(UnauthorizedError):
            auth_service.login(connection, email, "totally-wrong-password")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE email = %s", (email.lower(),))


@requires_db
def test_login_with_unknown_email_raises_unauthorized_not_not_found(connection):
    """Deliberately the same error as a wrong password, so the endpoint
    doesn't leak which emails are registered."""
    with pytest.raises(UnauthorizedError):
        auth_service.login(connection, _email(), "whatever")
