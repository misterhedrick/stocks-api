import httpx

from app.core.config import settings


class AlpacaClient:
    def __init__(self) -> None:
        self.headers = {
            'APCA-API-KEY-ID': settings.alpaca_api_key,
            'APCA-API-SECRET-KEY': settings.alpaca_secret_key,
        }

    async def get_account(self) -> dict:
        async with httpx.AsyncClient(base_url=settings.alpaca_base_url, headers=self.headers, timeout=20.0) as client:
            response = await client.get('/v2/account')
            response.raise_for_status()
            return response.json()
