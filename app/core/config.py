from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'stocks-api'
    env: str = 'dev'
    debug: bool = True
    api_v1_prefix: str = '/api/v1'

    admin_bearer_token: str = 'change-me'
    database_url: str = 'postgresql+psycopg://postgres:postgres@localhost:5432/stocks_api'

    alpaca_api_key: str = ''
    alpaca_secret_key: str = ''
    alpaca_base_url: str = 'https://paper-api.alpaca.markets'
    alpaca_data_url: str = 'https://data.alpaca.markets'


settings = Settings()
