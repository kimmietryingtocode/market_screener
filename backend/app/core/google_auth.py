from typing import Any

from app.core.config import settings
from app.services.errors import UnauthorizedError, UpstreamServiceError


def verify_google_id_token(credential: str) -> dict[str, Any]:
    if not settings.google_client_id:
        raise UpstreamServiceError("Google sign-in is not configured")

    try:
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport import requests
        from google.oauth2 import id_token
    except ImportError as exc:
        raise UpstreamServiceError("Google sign-in dependency is not installed") from exc

    try:
        claims = id_token.verify_oauth2_token(
            credential,
            requests.Request(),
            settings.google_client_id,
        )
    except ValueError as exc:
        raise UnauthorizedError("invalid Google credential") from exc
    except (GoogleAuthError, OSError) as exc:
        raise UpstreamServiceError("Google sign-in is temporarily unavailable") from exc

    if not claims.get("sub") or not claims.get("email") or claims.get("email_verified") is not True:
        raise UnauthorizedError("Google account email is not verified")
    return claims
