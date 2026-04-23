from alpaca.trading.client import TradingClient

from app.core.config import settings

trading_client = TradingClient(
    api_key=settings.alpaca_api_key,
    secret_key=settings.alpaca_api_secret,
    paper=settings.alpaca_paper,
)