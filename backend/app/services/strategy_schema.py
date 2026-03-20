from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Literal


TradeMode = Literal["PAPER", "LIVE"]
OptionSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: str
    user_id: int
    enabled: bool
    mode: TradeMode
    segment: str
    min_confidence_score: float
    allowed_side: OptionSide
    max_trades_per_day: int
    max_loss_per_day: float
    entry_start_time: time
    entry_end_time: time
    target_pct: float
    stop_loss_pct: float

