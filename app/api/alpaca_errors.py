from fastapi import status

from app.integrations.alpaca import AlpacaTradingError


def alpaca_error_status_code(exc: AlpacaTradingError) -> int:
    if exc.status_code in {400, 422}:
        return exc.status_code
    if exc.status_code == 401:
        return status.HTTP_502_BAD_GATEWAY
    if exc.status_code == 403:
        return status.HTTP_502_BAD_GATEWAY
    if exc.status_code == 404:
        return status.HTTP_502_BAD_GATEWAY
    if exc.status_code == 429:
        return status.HTTP_502_BAD_GATEWAY
    if exc.status_code is not None and 500 <= exc.status_code <= 599:
        return status.HTTP_502_BAD_GATEWAY
    return status.HTTP_502_BAD_GATEWAY
