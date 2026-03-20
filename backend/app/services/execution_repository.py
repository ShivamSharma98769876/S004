from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol


class SqlExecutor(Protocol):
    def execute(self, query: str, params: tuple) -> None: ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class LiveTradeRow:
    trade_ref: str
    recommendation_id: str
    user_id: int
    strategy_id: str
    symbol: str
    mode: str
    quantity: int
    entry_price: float
    target_price: float
    stop_loss_price: float
    current_state: str
    broker_order_id: str
    created_at: datetime


@dataclass(frozen=True)
class TradeEventRow:
    trade_ref: str
    event_type: str
    prev_state: str | None
    next_state: str | None
    reason_code: str | None
    event_payload_json: str
    occurred_at: datetime


@dataclass(frozen=True)
class ReconciliationIssueRow:
    run_id: str
    user_id: int
    symbol: str
    issue_type: str
    internal_value: str
    broker_value: str
    detected_at: datetime


class ExecutionRepository:
    _UPSERT_LIVE_TRADE = """
    INSERT INTO s004_live_trades (
      trade_ref, recommendation_id, user_id, strategy_id, symbol, mode,
      quantity, entry_price, target_price, stop_loss_price, current_state, broker_order_id, created_at
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (trade_ref)
    DO UPDATE SET
      current_state=EXCLUDED.current_state,
      broker_order_id=EXCLUDED.broker_order_id,
      updated_at=NOW();
    """

    _INSERT_EVENT = """
    INSERT INTO s004_trade_events (
      trade_ref, event_type, prev_state, next_state, reason_code, event_payload, occurred_at
    ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s);
    """

    _INSERT_RECON = """
    INSERT INTO s004_position_reconciliation (
      run_id, user_id, symbol, issue_type, internal_value, broker_value, detected_at
    ) VALUES (%s,%s,%s,%s,%s,%s,%s);
    """

    def __init__(self, db: SqlExecutor) -> None:
        self.db = db

    def upsert_live_trade(self, row: LiveTradeRow) -> None:
        self.db.execute(
            self._UPSERT_LIVE_TRADE,
            (
                row.trade_ref,
                row.recommendation_id,
                row.user_id,
                row.strategy_id,
                row.symbol,
                row.mode,
                row.quantity,
                row.entry_price,
                row.target_price,
                row.stop_loss_price,
                row.current_state,
                row.broker_order_id,
                row.created_at,
            ),
        )
        self.db.commit()

    def insert_event(self, row: TradeEventRow) -> None:
        self.db.execute(
            self._INSERT_EVENT,
            (
                row.trade_ref,
                row.event_type,
                row.prev_state,
                row.next_state,
                row.reason_code,
                row.event_payload_json,
                row.occurred_at,
            ),
        )
        self.db.commit()

    def insert_reconciliation_issues(self, rows: Iterable[ReconciliationIssueRow]) -> int:
        count = 0
        for r in rows:
            self.db.execute(
                self._INSERT_RECON,
                (
                    r.run_id,
                    r.user_id,
                    r.symbol,
                    r.issue_type,
                    r.internal_value,
                    r.broker_value,
                    r.detected_at,
                ),
            )
            count += 1
        self.db.commit()
        return count

