from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_daily_loss: float
    max_open_trades: int
    max_exposure_notional: float
    max_slippage_pct: float = 2.0


@dataclass(frozen=True)
class RiskContext:
    daily_realized_pnl: float
    open_trades_count: int
    current_exposure_notional: float
    proposed_notional: float


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason_code: str
    message: str


def evaluate_pre_trade_risk(limits: RiskLimits, ctx: RiskContext) -> RiskDecision:
    if abs(min(ctx.daily_realized_pnl, 0.0)) >= limits.max_daily_loss:
        return RiskDecision(False, "DAILY_LOSS_LIMIT_REACHED", "Daily loss limit reached")

    if ctx.open_trades_count >= limits.max_open_trades:
        return RiskDecision(False, "MAX_OPEN_TRADES_REACHED", "Maximum open trades reached")

    if (ctx.current_exposure_notional + ctx.proposed_notional) > limits.max_exposure_notional:
        return RiskDecision(False, "EXPOSURE_LIMIT_REACHED", "Exposure limit would be exceeded")

    return RiskDecision(True, "ALLOWED", "Risk checks passed")


def evaluate_post_trade_risk(fill_price: float, expected_price: float, max_slippage_pct: float) -> RiskDecision:
    if expected_price <= 0:
        return RiskDecision(False, "INVALID_EXPECTED_PRICE", "Expected price should be positive")
    slippage_pct = abs(fill_price - expected_price) / expected_price * 100.0
    if slippage_pct > max_slippage_pct:
        return RiskDecision(False, "SLIPPAGE_TOO_HIGH", f"Slippage {slippage_pct:.2f}% beyond threshold")
    return RiskDecision(True, "ALLOWED", "Post-trade checks passed")

