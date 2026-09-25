from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://localhost:5432/portfolio"
    jwt_secret: str
    quant_service_url: str = "http://localhost:8000"
    jwt_ttl_hours: int = 24

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
