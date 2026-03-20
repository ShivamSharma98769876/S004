from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass
class Tick:
    symbol: str
    price: float
    volume: int
    ts: datetime


@dataclass
class Candle:
    symbol: str
    bucket_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class CandleBuilder1m:
    """
    1-minute candle builder scaffold for W02-S03.
    """

    def __init__(self) -> None:
        self._active: Dict[str, Candle] = {}

    @staticmethod
    def _bucket(dt: datetime) -> datetime:
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.replace(second=0, microsecond=0)

    def on_tick(self, tick: Tick) -> Optional[Candle]:
        bucket = self._bucket(tick.ts)
        current = self._active.get(tick.symbol)

        # Start first candle for symbol
        if current is None:
            self._active[tick.symbol] = Candle(
                symbol=tick.symbol,
                bucket_start=bucket,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=max(0, tick.volume),
            )
            return None

        # If tick belongs to new minute, close previous candle and start next.
        if current.bucket_start != bucket:
            closed = current
            self._active[tick.symbol] = Candle(
                symbol=tick.symbol,
                bucket_start=bucket,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=max(0, tick.volume),
            )
            return closed

        # Update existing minute candle
        current.high = max(current.high, tick.price)
        current.low = min(current.low, tick.price)
        current.close = tick.price
        current.volume += max(0, tick.volume)
        return None

