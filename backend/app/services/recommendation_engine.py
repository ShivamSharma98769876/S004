from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict

try:
    from .policy_engine import evaluate_strategy_policy
    from .strategy_schema import StrategyConfig
except ImportError:  # pragma: no cover - direct script execution fallback
    from policy_engine import evaluate_strategy_policy
    from strategy_schema import StrategyConfig


@dataclass(frozen=True)
class RankedCandidate:
    instrument: str
    expiry: str
    symbol: str
    side: str
    confidence_score: float
    strike: float
    ltp: float
    factors: Dict[str, float]


@dataclass(frozen=True)
class TradeRecommendation:
    recommendation_id: str
    strategy_id: str
    user_id: int
    instrument: str
    expiry: str
    symbol: str
    side: str
    entry_price: float
    target_price: float
    stop_loss_price: float
    confidence_score: float
    reason_code: str
    created_at: datetime
    status: str  # GENERATED / SKIPPED


def build_recommendation(
    cfg: StrategyConfig,
    candidate: RankedCandidate,
    now: datetime,
    trades_taken_today: int,
    realized_loss_today: float,
) -> TradeRecommendation:
    decision = evaluate_strategy_policy(
        cfg=cfg,
        confidence_score=candidate.confidence_score,
        now=now,
        trades_taken_today=trades_taken_today,
        realized_loss_today=realized_loss_today,
    )

    rec_id = f"{cfg.strategy_id}-{cfg.user_id}-{int(now.timestamp())}"
    if not decision.allowed:
        return TradeRecommendation(
            recommendation_id=rec_id,
            strategy_id=cfg.strategy_id,
            user_id=cfg.user_id,
            instrument=candidate.instrument,
            expiry=candidate.expiry,
            symbol=candidate.symbol,
            side=candidate.side,
            entry_price=candidate.ltp,
            target_price=candidate.ltp,
            stop_loss_price=candidate.ltp,
            confidence_score=candidate.confidence_score,
            reason_code=decision.reason_code,
            created_at=now,
            status="SKIPPED",
        )

    entry = candidate.ltp
    target = entry * (1 + (cfg.target_pct / 100.0))
    stop = entry * (1 - (cfg.stop_loss_pct / 100.0))

    return TradeRecommendation(
        recommendation_id=rec_id,
        strategy_id=cfg.strategy_id,
        user_id=cfg.user_id,
        instrument=candidate.instrument,
        expiry=candidate.expiry,
        symbol=candidate.symbol,
        side=candidate.side,
        entry_price=round(entry, 4),
        target_price=round(target, 4),
        stop_loss_price=round(stop, 4),
        confidence_score=candidate.confidence_score,
        reason_code="TOP_RANKED_STRIKE",
        created_at=now,
        status="GENERATED",
    )

