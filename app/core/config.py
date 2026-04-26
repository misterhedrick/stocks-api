from decimal import Decimal
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
    alpaca_request_timeout_seconds: int = 10
    market_cycle_scan_enabled: bool = True
    market_cycle_reconcile_enabled: bool = True
    market_cycle_preview_enabled: bool = False
    market_cycle_submit_enabled: bool = False
    trading_automation_enabled: bool = False
    auto_submit_requires_paper: bool = True
    max_auto_orders_per_cycle: int = 1
    max_auto_orders_per_day: int = 3
    max_open_positions: int = 3
    max_open_positions_per_symbol: int = 1
    max_contracts_per_order: int = 1
    max_estimated_premium_per_order: Decimal = Decimal("250")
    auto_migrate_on_startup: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUTO_MIGRATE_ON_STARTUP",
            "APP_AUTO_MIGRATE_ON_STARTUP",
        ),
    )

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

    @property
    def should_auto_migrate_on_startup(self) -> bool:
        if self.auto_migrate_on_startup is not None:
            return self.auto_migrate_on_startup
        return self.environment.strip().lower() in {"production", "staging"}

    @property
    def alpaca_trading_base_url(self) -> str:
        if self.alpaca_paper:
            return "https://paper-api.alpaca.markets"
        return "https://api.alpaca.markets"

    @property
    def alpaca_data_base_url(self) -> str:
        return "https://data.alpaca.markets"


settings = Settings()
