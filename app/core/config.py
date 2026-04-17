from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "stocks-api"
    environment: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    admin_bearer_token: str = "change-me"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/stocks_api"
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_paper: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
