from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

try:
    from .strategy_schema import StrategyConfig
except ImportError:  # pragma: no cover - direct script execution fallback
    from strategy_schema import StrategyConfig


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str
    message: str


def evaluate_strategy_policy(
    cfg: StrategyConfig,
    confidence_score: float,
    now: datetime,
    trades_taken_today: int,
    realized_loss_today: float,
) -> PolicyDecision:
    if not cfg.enabled:
        return PolicyDecision(False, "STRATEGY_DISABLED", "Strategy is disabled")
    if confidence_score < cfg.min_confidence_score:
        return PolicyDecision(False, "LOW_CONFIDENCE", "Confidence score below threshold")
    if trades_taken_today >= cfg.max_trades_per_day:
        return PolicyDecision(False, "TRADE_LIMIT_REACHED", "Daily trade limit reached")
    if abs(realized_loss_today) >= cfg.max_loss_per_day:
        return PolicyDecision(False, "LOSS_LIMIT_REACHED", "Daily loss limit reached")

    current_t = now.time()
    if current_t < cfg.entry_start_time or current_t > cfg.entry_end_time:
        return PolicyDecision(False, "OUTSIDE_ENTRY_WINDOW", "Outside allowed entry time window")

    return PolicyDecision(True, "ALLOWED", "Strategy policy passed")

