from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, field_serializer


def _utc_wall_iso_z(dt: datetime | None) -> str | None:
    """DB trade timestamps are UTC wall in ``TIMESTAMP WITHOUT TIME ZONE``; JSON must use ``Z`` so browsers parse as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


class StrategyItemOut(BaseModel):
    strategy_id: str
    version: str
    display_name: str
    description: str
    strategy_details: dict[str, Any] | None = None
    strategy_explainer: str
    risk_profile: Literal["LOW", "MEDIUM", "HIGH"]
    status: str
    publish_status: str
    pnl_30d: float
    win_rate: float
    position_intent: Literal["long_premium", "short_premium"] | None = None


class StrategyDetailsPayload(BaseModel):
    details: dict[str, Any]


class CreateStrategyPayload(BaseModel):
    strategy_id: str
    version: str = "1.0.0"
    display_name: str
    description: str = ""
    risk_profile: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    details: dict[str, Any] | None = None


class SubscriptionPayload(BaseModel):
    strategy_id: str
    strategy_version: str
    mode: str = "PAPER"
    action: str


class SubscriptionResponse(BaseModel):
    status: str
    subscription_status: str


class ExecuteRequest(BaseModel):
    recommendation_id: str
    mode: str = "PAPER"
    quantity: int = 1


class ExecuteResponse(BaseModel):
    status: str
    trade_ref: str
    order_ref: str


class RecommendationOut(BaseModel):
    recommendation_id: str
    symbol: str
    instrument: str
    expiry: str
    side: str
    entry_price: float
    target_price: float
    stop_loss_price: float
    confidence_score: float
    rank_value: int
    vwap: float | None = None
    ema9: float | None = None
    ema21: float | None = None
    rsi: float | None = None
    ivr: float | None = None
    volume: float | None = None
    avg_volume: float | None = None
    volume_spike_ratio: float | None = None
    score: float | int | None = None  # rule-based uses int; heuristic uses float (weighted avg)
    score_max: float | int | None = None
    primary_ok: bool | None = None
    ema_ok: bool | None = None
    rsi_ok: bool | None = None
    volume_ok: bool | None = None
    signal_eligible: bool | None = None
    failed_conditions: str | None = None
    heuristic_reasons: list[str] | None = None
    strategy_name: str | None = None
    spot_price: float | None = None
    atm_distance: int | None = None
    timeframe: str | None = None
    refresh_interval_sec: int | None = None
    status: str
    created_at: datetime
    strategy_id: str | None = None
    strategy_version: str | None = None
    trendpulse: dict[str, Any] | None = None
    option_type: str | None = None
    delta: float | None = None
    gamma: float | None = None
    oi: int | None = None


def _format_exit_reason(reason_code: str | None) -> str:
    if reason_code == "TARGET_HIT":
        return "Target Hit"
    if reason_code == "SL_HIT":
        return "SL Triggered"
    if reason_code in ("ADMIN_CLOSE", "ADMIN", "FORCED_EXIT"):
        return "Admin Close"
    if reason_code in ("MANUAL", "MANUAL_EXECUTE", "USER_EXIT"):
        return "Manual"
    if not reason_code:
        return "Unknown"
    return str(reason_code).replace("_", " ").title()


class TradeOut(BaseModel):
    trade_ref: str
    symbol: str
    mode: str
    side: str
    quantity: int
    qty: int | None = None  # LotSize × quantity (contracts); display this as QTY
    entry_price: float
    current_price: float
    target_price: float
    stop_loss_price: float
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    updated_at: datetime
    current_state: str | None = None
    reason: str | None = None
    manual_execute: bool | None = None  # True=Manual, False=Auto; display as "Manual" or "Auto"
    score: float | int | None = None
    confidence_score: float | None = None
    strategy_name: str | None = None

    @field_serializer("opened_at", "closed_at", "updated_at", when_used="json")
    def _serialize_trade_timestamps(self, v: datetime | None) -> str | None:
        return _utc_wall_iso_z(v)


class SettingsPayload(BaseModel):
    master: dict[str, Any]
    credentials: dict[str, Any]
    capitalRisk: dict[str, Any]
    tradingParameters: dict[str, Any]
    strategy: dict[str, Any]


class SettingsResponse(BaseModel):
    master: dict[str, Any]
    credentials: dict[str, Any]
    capitalRisk: dict[str, Any]
    tradingParameters: dict[str, Any]
    strategy: dict[str, Any]
    updatedAt: str
