from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.models import JobRun


@dataclass(slots=True)
class MarketCycleResult:
    job_run: JobRun
    scan_enabled: bool
    reconcile_enabled: bool
    preview_enabled: bool
    exit_enabled: bool
    news_enabled: bool
    submit_enabled: bool
    scan: dict[str, Any] | None
    reconcile: dict[str, Any] | None
    preview: dict[str, Any] | None
    exits: dict[str, Any] | None
    news: dict[str, Any] | None
    submit: dict[str, Any] | None
    timings: dict[str, float] | None = None
    phase_timeout_seconds: int | None = None
    diagnostics: dict[str, Any] | None = None
    symbol: str | None = None


_MARKET_CYCLE_LOCK_KEY = 4_096_001
_MARKET_ENTRY_LOCK_BASE_KEY = 4_096_100
SUPPORTED_MARKET_ENTRY_SYMBOLS = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA")


EXPOSURE_BROKER_ORDER_STATUSES = (
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "filled",
    "submitted",
)


def normalize_market_entry_symbol(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    if normalized is None:
        raise ValueError("symbol is required")
    if normalized not in SUPPORTED_MARKET_ENTRY_SYMBOLS:
        supported = ", ".join(SUPPORTED_MARKET_ENTRY_SYMBOLS)
        raise ValueError(f"unsupported symbol {normalized!r}; supported symbols: {supported}")
    return normalized


def _normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    return normalized or None


def _market_entry_lock_key(symbol: str) -> int:
    return _MARKET_ENTRY_LOCK_BASE_KEY + SUPPORTED_MARKET_ENTRY_SYMBOLS.index(symbol)
