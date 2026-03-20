from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass
class Candle:
    close: float
    high: float
    low: float
    volume: float


def ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    if period <= 1:
        return values[-1]
    k = 2 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = (v * k) + (result * (1 - k))
    return result


def rsi(values: List[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def vwap(candles: Iterable[Candle]) -> float:
    total_pv = 0.0
    total_v = 0.0
    for c in candles:
        typical_price = (c.high + c.low + c.close) / 3
        total_pv += typical_price * c.volume
        total_v += c.volume
    return total_pv / total_v if total_v > 0 else 0.0

