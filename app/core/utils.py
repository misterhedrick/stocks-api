from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


def current_trading_day_start_utc() -> datetime:
    """Return midnight ET of the current trading day as a UTC datetime."""
    trading_tz = ZoneInfo("America/New_York")
    local_now = datetime.now(timezone.utc).astimezone(trading_tz)
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=trading_tz)
    return local_start.astimezone(timezone.utc)
