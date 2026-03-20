from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol


class SqlExecutor(Protocol):
    def execute(self, query: str, params: tuple) -> None: ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class RecommendationRow:
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
    status: str
    created_at: datetime


class RecommendationRepository:
    _UPSERT = """
    INSERT INTO s004_trade_recommendations (
      recommendation_id, strategy_id, user_id, instrument, expiry, symbol, side,
      entry_price, target_price, stop_loss_price, confidence_score, reason_code, status, created_at
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (recommendation_id)
    DO UPDATE SET
      confidence_score=EXCLUDED.confidence_score,
      reason_code=EXCLUDED.reason_code,
      status=EXCLUDED.status,
      updated_at=NOW();
    """

    def __init__(self, db: SqlExecutor) -> None:
        self.db = db

    def save_batch(self, rows: Iterable[RecommendationRow]) -> int:
        count = 0
        for r in rows:
            self.db.execute(
                self._UPSERT,
                (
                    r.recommendation_id,
                    r.strategy_id,
                    r.user_id,
                    r.instrument,
                    r.expiry,
                    r.symbol,
                    r.side,
                    r.entry_price,
                    r.target_price,
                    r.stop_loss_price,
                    r.confidence_score,
                    r.reason_code,
                    r.status,
                    r.created_at,
                ),
            )
            count += 1
        self.db.commit()
        return count

