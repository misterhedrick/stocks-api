from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "stocks-api"
    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )
    debug: bool = Field(
        default=True,
        validation_alias=AliasChoices("APP_DEBUG", "DEBUG"),
    )
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    admin_api_token: str = "change-me"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/stocks_api"
    database_connect_timeout_seconds: int = 5

    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_paper: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value: Any) -> Any:
        if isinstance(value, str) and value.lower() in {"release", "production", "prod"}:
            return False
        return value

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url


settings = Settings()
