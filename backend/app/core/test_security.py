"""Auth token tests.

These cover the classic JWT failure modes rather than just the happy path,
because every authenticated endpoint in the app trusts whatever
current_user_id returns.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.config import settings
from app.core.security import create_access_token, current_user_id, hash_password, verify_password


def _credentials(token: str, scheme: str = "Bearer") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)


# --- password hashing -----------------------------------------------------

def test_password_verifies_against_its_own_hash():
    hashed = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", hashed) is True


def test_wrong_password_does_not_verify():
    assert verify_password("wrong", hash_password("correct-horse-battery")) is False


def test_hash_is_salted_so_equal_passwords_differ():
    """Identical passwords must not produce identical hashes, or the hash
    table leaks which users share a password."""
    assert hash_password("same-password") != hash_password("same-password")


def test_hash_does_not_contain_the_plaintext():
    assert "correct-horse-battery" not in hash_password("correct-horse-battery")


# --- token round trip -----------------------------------------------------

def test_valid_token_resolves_to_its_user_id():
    assert current_user_id(_credentials(create_access_token(4242))) == 4242


def test_missing_credentials_are_rejected():
    with pytest.raises(HTTPException) as exc:
        current_user_id(None)
    assert exc.value.status_code == 401


def test_non_bearer_scheme_is_rejected():
    with pytest.raises(HTTPException) as exc:
        current_user_id(_credentials(create_access_token(1), scheme="Basic"))
    assert exc.value.status_code == 401


# --- the attacks that matter ----------------------------------------------

def test_expired_token_is_rejected():
    expired = jwt.encode(
        {"user_id": 1, "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        current_user_id(_credentials(expired))
    assert exc.value.status_code == 401


def test_token_signed_with_a_different_secret_is_rejected():
    forged = jwt.encode(
        {"user_id": 1, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "not-the-real-secret-but-long-enough-to-be-plausible",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        current_user_id(_credentials(forged))
    assert exc.value.status_code == 401


def test_unsigned_alg_none_token_is_rejected():
    """The classic JWT bypass: re-sign the payload with alg=none. Pinning
    algorithms=['HS256'] on decode is what stops it."""
    unsigned = jwt.encode({"user_id": 1}, key="", algorithm="none")
    with pytest.raises(HTTPException) as exc:
        current_user_id(_credentials(unsigned))
    assert exc.value.status_code == 401


def test_token_without_a_user_id_claim_is_rejected():
    no_subject = jwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        current_user_id(_credentials(no_subject))
    assert exc.value.status_code == 401


def test_token_with_a_non_numeric_user_id_is_rejected():
    """A string user_id must not slip through into SQL parameters."""
    tampered = jwt.encode(
        {"user_id": "1 OR 1=1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        current_user_id(_credentials(tampered))
    assert exc.value.status_code == 401


def test_garbage_token_is_rejected_without_raising_something_other_than_401():
    for junk in ("", "not.a.token", "a.b.c", "x" * 500):
        with pytest.raises(HTTPException) as exc:
            current_user_id(_credentials(junk))
        assert exc.value.status_code == 401


def test_issued_token_carries_an_expiry():
    """A token without exp never expires, so a leaked one is valid forever."""
    payload = jwt.decode(create_access_token(7), settings.jwt_secret, algorithms=["HS256"])
    assert "exp" in payload
    assert payload["exp"] > datetime.now(timezone.utc).timestamp()
