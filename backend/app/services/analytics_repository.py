from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Protocol


class SqlExecutor(Protocol):
    def execute(self, query: str, params: tuple) -> None: ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class OptionChainRecord:
    instrument: str
    expiry: str
    strike: float
    option_type: str
    ltp: float
    volume: int
    open_interest: int
    oi_change_pct: float
    captured_at: datetime


@dataclass(frozen=True)
class GreeksRecord:
    instrument: str
    expiry: str
    strike: float
    option_type: str
    delta: float
    gamma: float
    theta: float
    vega: float
    iv_pct: float
    model_version: str
    captured_at: datetime


class AnalyticsRepository:
    """
    Persistence repository for W03-S04.

    Uses upsert semantics so repeated cycle writes for the same
    strike/option_type/captured_at do not create duplicates.
    """

    _UPSERT_OPTION_CHAIN = """
    INSERT INTO s004_option_chain (
      instrument, expiry, strike, option_type, ltp, volume, open_interest, oi_change_pct, captured_at
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (instrument, expiry, strike, option_type, captured_at)
    DO UPDATE SET
      ltp=EXCLUDED.ltp,
      volume=EXCLUDED.volume,
      open_interest=EXCLUDED.open_interest,
      oi_change_pct=EXCLUDED.oi_change_pct;
    """

    _UPSERT_GREEKS = """
    INSERT INTO s004_option_greeks (
      instrument, expiry, strike, option_type, delta, gamma, theta, vega, iv_pct, model_version, captured_at
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (instrument, expiry, strike, option_type, captured_at)
    DO UPDATE SET
      delta=EXCLUDED.delta,
      gamma=EXCLUDED.gamma,
      theta=EXCLUDED.theta,
      vega=EXCLUDED.vega,
      iv_pct=EXCLUDED.iv_pct,
      model_version=EXCLUDED.model_version;
    """

    _INSERT_RUN = """
    INSERT INTO s004_analytics_pipeline_runs (run_id, status, records_written, error_message, started_at, completed_at)
    VALUES (%s,%s,%s,%s,%s,%s)
    ON CONFLICT (run_id)
    DO UPDATE SET
      status=EXCLUDED.status,
      records_written=EXCLUDED.records_written,
      error_message=EXCLUDED.error_message,
      completed_at=EXCLUDED.completed_at;
    """

    def __init__(self, db: SqlExecutor) -> None:
        self.db = db

    def save_option_chain_batch(self, rows: Iterable[OptionChainRecord]) -> int:
        count = 0
        for r in rows:
            self.db.execute(
                self._UPSERT_OPTION_CHAIN,
                (
                    r.instrument,
                    r.expiry,
                    r.strike,
                    r.option_type,
                    r.ltp,
                    r.volume,
                    r.open_interest,
                    r.oi_change_pct,
                    r.captured_at,
                ),
            )
            count += 1
        self.db.commit()
        return count

    def save_greeks_batch(self, rows: Iterable[GreeksRecord]) -> int:
        count = 0
        for r in rows:
            self.db.execute(
                self._UPSERT_GREEKS,
                (
                    r.instrument,
                    r.expiry,
                    r.strike,
                    r.option_type,
                    r.delta,
                    r.gamma,
                    r.theta,
                    r.vega,
                    r.iv_pct,
                    r.model_version,
                    r.captured_at,
                ),
            )
            count += 1
        self.db.commit()
        return count

    def upsert_pipeline_run(
        self,
        run_id: str,
        status: str,
        records_written: int,
        started_at: datetime,
        completed_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
    ) -> None:
        self.db.execute(
            self._INSERT_RUN,
            (run_id, status, records_written, error_message, started_at, completed_at),
        )
        self.db.commit()

