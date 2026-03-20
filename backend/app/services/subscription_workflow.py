from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Literal


SubscriptionStatus = Literal["ACTIVE", "PAUSED", "STOPPED"]
TradeMode = Literal["PAPER", "LIVE"]


@dataclass(frozen=True)
class SubscriptionRequest:
    user_id: int
    strategy_id: str
    strategy_version: str
    mode: TradeMode
    user_config: Dict[str, float]


@dataclass(frozen=True)
class SubscriptionDecision:
    allowed: bool
    reason_code: str
    message: str


@dataclass(frozen=True)
class SubscriptionRecord:
    user_id: int
    strategy_id: str
    strategy_version: str
    mode: TradeMode
    status: SubscriptionStatus
    user_config: Dict[str, float]
    updated_at: datetime


def validate_subscription_request(req: SubscriptionRequest) -> SubscriptionDecision:
    if not req.strategy_id or not req.strategy_version:
        return SubscriptionDecision(False, "INVALID_STRATEGY_REFERENCE", "Strategy id/version required")

    max_loss = float(req.user_config.get("max_loss_per_day", 0.0))
    if max_loss <= 0:
        return SubscriptionDecision(False, "INVALID_CONFIG", "max_loss_per_day must be > 0")

    max_trades = int(req.user_config.get("max_trades_per_day", 0))
    if max_trades <= 0:
        return SubscriptionDecision(False, "INVALID_CONFIG", "max_trades_per_day must be > 0")

    return SubscriptionDecision(True, "ALLOWED", "Subscription request is valid")


def subscribe(req: SubscriptionRequest) -> SubscriptionRecord:
    return SubscriptionRecord(
        user_id=req.user_id,
        strategy_id=req.strategy_id,
        strategy_version=req.strategy_version,
        mode=req.mode,
        status="ACTIVE",
        user_config=req.user_config,
        updated_at=datetime.utcnow(),
    )


def pause(record: SubscriptionRecord) -> SubscriptionRecord:
    return SubscriptionRecord(
        user_id=record.user_id,
        strategy_id=record.strategy_id,
        strategy_version=record.strategy_version,
        mode=record.mode,
        status="PAUSED",
        user_config=record.user_config,
        updated_at=datetime.utcnow(),
    )


def resume(record: SubscriptionRecord) -> SubscriptionRecord:
    return SubscriptionRecord(
        user_id=record.user_id,
        strategy_id=record.strategy_id,
        strategy_version=record.strategy_version,
        mode=record.mode,
        status="ACTIVE",
        user_config=record.user_config,
        updated_at=datetime.utcnow(),
    )

