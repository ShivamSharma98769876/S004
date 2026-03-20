from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List

try:
    from .recommendation_engine import RankedCandidate, TradeRecommendation, build_recommendation
    from .recommendation_repository import RecommendationRepository, RecommendationRow
    from .strategy_schema import StrategyConfig
except ImportError:  # pragma: no cover - direct script execution fallback
    from recommendation_engine import RankedCandidate, TradeRecommendation, build_recommendation
    from recommendation_repository import RecommendationRepository, RecommendationRow
    from strategy_schema import StrategyConfig


@dataclass(frozen=True)
class W04RankedStrike:
    symbol: str
    strike: float
    option_type: str
    confidence_score: float
    volume: float
    oi_change: float
    atm_distance: float


@dataclass(frozen=True)
class W04TopPack:
    instrument: str
    expiry: str
    ce: List[W04RankedStrike]
    pe: List[W04RankedStrike]
    cycle_ts: datetime


def _to_candidate(
    pack: W04TopPack,
    strike: W04RankedStrike,
    ltp_by_symbol: Dict[str, float],
) -> RankedCandidate:
    ltp = float(ltp_by_symbol.get(strike.symbol, 0.0))
    if ltp <= 0:
        # fallback approximation if LTP map is unavailable for a symbol
        ltp = max(1.0, strike.strike * 0.01)
    return RankedCandidate(
        instrument=pack.instrument,
        expiry=pack.expiry,
        symbol=strike.symbol,
        side="BUY",
        confidence_score=strike.confidence_score,
        strike=strike.strike,
        ltp=ltp,
        factors={
            "volume": strike.volume,
            "oi_change": strike.oi_change,
            "atm_distance": strike.atm_distance,
        },
    )


def generate_and_persist_recommendations(
    strategy: StrategyConfig,
    pack: W04TopPack,
    ltp_by_symbol: Dict[str, float],
    recommendation_repo: RecommendationRepository,
    trades_taken_today: int,
    realized_loss_today: float,
) -> List[TradeRecommendation]:
    """
    Hook W04 top-ranked output into W05 recommendation lifecycle.

    - Converts top CE/PE ranked strikes into recommendation candidates.
    - Applies strategy policy.
    - Persists GENERATED/SKIPPED rows in s004_trade_recommendations.
    """
    now = datetime.utcnow()
    generated: List[TradeRecommendation] = []
    rows: List[RecommendationRow] = []

    for ranked in [*pack.ce, *pack.pe]:
        candidate = _to_candidate(pack=pack, strike=ranked, ltp_by_symbol=ltp_by_symbol)
        rec = build_recommendation(
            cfg=strategy,
            candidate=candidate,
            now=now,
            trades_taken_today=trades_taken_today,
            realized_loss_today=realized_loss_today,
        )
        generated.append(rec)
        rows.append(
            RecommendationRow(
                recommendation_id=rec.recommendation_id,
                strategy_id=rec.strategy_id,
                user_id=rec.user_id,
                instrument=rec.instrument,
                expiry=rec.expiry,
                symbol=rec.symbol,
                side=rec.side,
                entry_price=rec.entry_price,
                target_price=rec.target_price,
                stop_loss_price=rec.stop_loss_price,
                confidence_score=rec.confidence_score,
                reason_code=rec.reason_code,
                status=rec.status,
                created_at=rec.created_at,
            )
        )

    recommendation_repo.save_batch(rows)
    return generated

