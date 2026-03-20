from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict

try:
    from .execution_repository import ExecutionRepository, ReconciliationIssueRow
    from .portfolio_reconciliation import Position, reconcile_positions
except ImportError:  # pragma: no cover - direct script execution fallback
    from execution_repository import ExecutionRepository, ReconciliationIssueRow
    from portfolio_reconciliation import Position, reconcile_positions


@dataclass(frozen=True)
class ReconciliationRunResult:
    run_id: str
    issue_count: int


def run_reconciliation(
    user_id: int,
    internal_positions: Dict[str, Position],
    broker_positions: Dict[str, Position],
    repo: ExecutionRepository,
) -> ReconciliationRunResult:
    run_id = f"RECON-{user_id}-{int(datetime.utcnow().timestamp())}"
    issues = reconcile_positions(internal_positions=internal_positions, broker_positions=broker_positions)
    now = datetime.utcnow()
    rows = [
        ReconciliationIssueRow(
            run_id=run_id,
            user_id=user_id,
            symbol=i.symbol,
            issue_type=i.issue_type,
            internal_value=i.internal_value,
            broker_value=i.broker_value,
            detected_at=now,
        )
        for i in issues
    ]
    if rows:
        repo.insert_reconciliation_issues(rows)
    return ReconciliationRunResult(run_id=run_id, issue_count=len(rows))

