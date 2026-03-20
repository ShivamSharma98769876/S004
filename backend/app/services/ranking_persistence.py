from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol


class SqlExecutor(Protocol):
    def execute(self, query: str, params: tuple) -> None: ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class StrikeScoreRecord:
    instrument: str
    expiry: str
    strike: float
    option_type: str
    confidence_score: float
    technical_score: float
    volume_score: float
    oi_score: float
    greeks_score: float
    liquidity_score: float
    rank_value: int
    cycle_ts: datetime
    model_version: str = "v1"


class RankingRepository:
    _UPSERT_SCORE = """
    INSERT INTO s004_strike_scores (
      instrument, expiry, strike, option_type, confidence_score, technical_score,
      volume_score, oi_score, greeks_score, liquidity_score, rank_value, cycle_ts, model_version
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (instrument, expiry, strike, option_type, cycle_ts)
    DO UPDATE SET
      confidence_score=EXCLUDED.confidence_score,
      technical_score=EXCLUDED.technical_score,
      volume_score=EXCLUDED.volume_score,
      oi_score=EXCLUDED.oi_score,
      greeks_score=EXCLUDED.greeks_score,
      liquidity_score=EXCLUDED.liquidity_score,
      rank_value=EXCLUDED.rank_value,
      model_version=EXCLUDED.model_version;
    """

    def __init__(self, db: SqlExecutor) -> None:
        self.db = db

    def save_scores(self, rows: Iterable[StrikeScoreRecord]) -> int:
        count = 0
        for r in rows:
            self.db.execute(
                self._UPSERT_SCORE,
                (
                    r.instrument,
                    r.expiry,
                    r.strike,
                    r.option_type,
                    r.confidence_score,
                    r.technical_score,
                    r.volume_score,
                    r.oi_score,
                    r.greeks_score,
                    r.liquidity_score,
                    r.rank_value,
                    r.cycle_ts,
                    r.model_version,
                ),
            )
            count += 1
        self.db.commit()
        return count

