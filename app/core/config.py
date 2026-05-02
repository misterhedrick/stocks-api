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
    market_cycle_exit_enabled: bool = False
    market_cycle_news_enabled: bool = False
    market_cycle_submit_enabled: bool = False
    market_cycle_phase_timeout_seconds: int = 70
    news_request_timeout_seconds: int = 10
    news_market_rss_feeds: str = (
        "https://news.google.com/rss/search?q=stock%20market%20OR%20S%26P%20500%20OR%20Nasdaq%20OR%20Dow%20Jones&hl=en-US&gl=US&ceid=US:en,"
        "https://news.google.com/rss/search?q=Federal%20Reserve%20OR%20interest%20rates%20OR%20inflation%20OR%20CPI%20OR%20PPI%20OR%20jobs%20report&hl=en-US&gl=US&ceid=US:en,"
        "https://news.google.com/rss/search?q=US%20economy%20OR%20Treasury%20yields%20OR%20dollar%20OR%20recession%20OR%20GDP&hl=en-US&gl=US&ceid=US:en,"
        "https://news.google.com/rss/search?q=world%20markets%20OR%20global%20stocks%20OR%20geopolitical%20risk%20OR%20oil%20prices%20OR%20war%20OR%20tariffs&hl=en-US&gl=US&ceid=US:en,"
        "https://news.google.com/rss/search?q=earnings%20guidance%20OR%20market%20volatility%20OR%20VIX%20OR%20credit%20markets%20OR%20banking%20sector&hl=en-US&gl=US&ceid=US:en"
    )
    news_ticker_rss_template: str = (
        "https://news.google.com/rss/search?q={symbol}%20stock%20OR%20{symbol}%20options&hl=en-US&gl=US&ceid=US:en"
    )
    trading_automation_enabled: bool = False
    auto_submit_requires_paper: bool = True
    max_auto_orders_per_cycle: int = 1
    max_auto_orders_per_day: int = 3
    max_open_positions: int = 3
    max_open_positions_per_symbol: int = 1
    max_contracts_per_order: int = 1
    max_estimated_premium_per_order: Decimal = Decimal("250")
    paper_strategy_min_change_percent: Decimal = Decimal("0.10")
    paper_strategy_trend_min_change_percent: Decimal = Decimal("0.35")
    paper_strategy_max_spread: Decimal = Decimal("0.20")
    paper_strategy_max_spread_percent: Decimal = Decimal("20")
    paper_strategy_profit_target_percent: Decimal = Decimal("25")
    paper_strategy_stop_loss_percent: Decimal = Decimal("15")
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
