from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Catches the exact placeholder text in .env.example shipping into a real
# deploy unfilled, and other values nobody would pick on purpose.
_REJECTED_JWT_SECRETS = {
    "replace-with-a-long-random-local-secret",
    "secret",
    "changeme",
    "password",
}


class Settings(BaseSettings):
    database_url: str = "postgresql://localhost:5432/portfolio"
    jwt_secret: str
    quant_service_url: str = "http://localhost:8000"
    jwt_ttl_hours: int = 24
    redis_url: str | None = None
    log_level: str = "INFO"
    # Browsers block cross-origin API calls unless the server opts in, so the
    # frontend's origin has to be listed here. Comma-separated; empty means
    # no cross-origin access is permitted.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_must_be_strong(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters — a short or guessable "
                "secret lets anyone forge auth tokens. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if value.lower() in _REJECTED_JWT_SECRETS:
            raise ValueError("JWT_SECRET is a placeholder/example value, not a real secret")
        return value


settings = Settings()
