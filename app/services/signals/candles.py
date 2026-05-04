from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Candle:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CandleFrame:
    symbol: str
    timeframe: str
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if not self.timeframe.strip():
            raise ValueError("timeframe is required")
        if any(self.candles[index].ts > self.candles[index + 1].ts for index in range(len(self.candles) - 1)):
            raise ValueError("candles must be sorted by timestamp ascending")

    @property
    def latest(self) -> Candle | None:
        return self.candles[-1] if self.candles else None

    @property
    def previous(self) -> Candle | None:
        return self.candles[-2] if len(self.candles) >= 2 else None

    @property
    def closes(self) -> list[float]:
        return [float(candle.close) for candle in self.candles]

    @property
    def highs(self) -> list[float]:
        return [float(candle.high) for candle in self.candles]

    @property
    def lows(self) -> list[float]:
        return [float(candle.low) for candle in self.candles]

    @property
    def volumes(self) -> list[float | None]:
        return [float(candle.volume) if candle.volume is not None else None for candle in self.candles]

    def is_stale(self, *, max_age_seconds: int, now: datetime | None = None) -> bool:
        latest = self.latest
        if latest is None:
            return True
        current_time = now or datetime.now(timezone.utc)
        latest_ts = latest.ts
        if latest_ts.tzinfo is None:
            latest_ts = latest_ts.replace(tzinfo=timezone.utc)
        return (current_time - latest_ts).total_seconds() > max_age_seconds


def candle_frame_from_dicts(
    *,
    symbol: str,
    timeframe: str,
    rows: Iterable[dict],
) -> CandleFrame:
    candles = []
    for row in rows:
        candles.append(
            Candle(
                ts=row["ts"],
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])) if row.get("volume") is not None else None,
            )
        )
    return CandleFrame(symbol=symbol, timeframe=timeframe, candles=tuple(candles))
