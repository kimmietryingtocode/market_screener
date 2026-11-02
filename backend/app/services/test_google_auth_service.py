import jwt
import pytest

from app.core.config import settings
from app.services import auth_service
from app.services.errors import ConflictError, UnauthorizedError


def _claims(**overrides):
    return {
        "sub": "google-user-123",
        "email": "person@example.com",
        "email_verified": True,
        "name": "Person Example",
        **overrides,
    }


def test_google_login_reuses_the_stable_google_subject(monkeypatch):
    monkeypatch.setattr(auth_service, "verify_google_id_token", lambda credential: _claims())
    monkeypatch.setattr(
        auth_service.users_repository,
        "find_by_google_sub",
        lambda connection, google_sub: (42, "person@example.com"),
    )

    token, email, display_name = auth_service.google_login(object(), "credential")

    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert payload["user_id"] == 42
    assert (email, display_name) == ("person@example.com", "Person Example")


def test_google_login_does_not_silently_link_a_password_account(monkeypatch):
    monkeypatch.setattr(auth_service, "verify_google_id_token", lambda credential: _claims())
    monkeypatch.setattr(auth_service.users_repository, "find_by_google_sub", lambda *args: None)
    monkeypatch.setattr(auth_service.users_repository, "find_by_email", lambda *args: (7, "hash"))

    with pytest.raises(ConflictError, match="sign in with your password"):
        auth_service.google_login(object(), "credential")


def test_google_login_creates_a_new_federated_user(monkeypatch):
    inserted = {}
    monkeypatch.setattr(auth_service, "verify_google_id_token", lambda credential: _claims())
    monkeypatch.setattr(auth_service.users_repository, "find_by_google_sub", lambda *args: None)
    monkeypatch.setattr(auth_service.users_repository, "find_by_email", lambda *args: None)
    monkeypatch.setattr(auth_service, "hash_password", lambda password: "generated-hash")

    def insert(connection, email, password_hash, google_sub):
        inserted.update(email=email, password_hash=password_hash, google_sub=google_sub)
        return 9

    monkeypatch.setattr(auth_service.users_repository, "insert_google_user", insert)

    token, email, _ = auth_service.google_login(object(), "credential")

    assert jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])["user_id"] == 9
    assert email == "person@example.com"
    assert inserted == {
        "email": "person@example.com",
        "password_hash": "generated-hash",
        "google_sub": "google-user-123",
    }


def test_google_login_rejects_unverified_email(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "verify_google_id_token",
        lambda credential: (_ for _ in ()).throw(UnauthorizedError("Google account email is not verified")),
    )

    with pytest.raises(UnauthorizedError, match="not verified"):
        auth_service.google_login(object(), "credential")
