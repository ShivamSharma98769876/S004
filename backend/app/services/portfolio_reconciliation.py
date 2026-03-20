from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    avg_price: float


@dataclass(frozen=True)
class ReconciliationIssue:
    symbol: str
    issue_type: str
    internal_value: str
    broker_value: str


def reconcile_positions(
    internal_positions: Dict[str, Position],
    broker_positions: Dict[str, Position],
) -> List[ReconciliationIssue]:
    issues: List[ReconciliationIssue] = []
    all_symbols = set(internal_positions.keys()) | set(broker_positions.keys())

    for symbol in sorted(all_symbols):
        ip = internal_positions.get(symbol)
        bp = broker_positions.get(symbol)

        if ip is None:
            issues.append(
                ReconciliationIssue(
                    symbol=symbol,
                    issue_type="MISSING_INTERNAL",
                    internal_value="none",
                    broker_value=f"qty={bp.quantity},avg={bp.avg_price}" if bp else "none",
                )
            )
            continue
        if bp is None:
            issues.append(
                ReconciliationIssue(
                    symbol=symbol,
                    issue_type="MISSING_BROKER",
                    internal_value=f"qty={ip.quantity},avg={ip.avg_price}",
                    broker_value="none",
                )
            )
            continue

        if ip.quantity != bp.quantity:
            issues.append(
                ReconciliationIssue(
                    symbol=symbol,
                    issue_type="QTY_MISMATCH",
                    internal_value=str(ip.quantity),
                    broker_value=str(bp.quantity),
                )
            )
        if round(ip.avg_price, 4) != round(bp.avg_price, 4):
            issues.append(
                ReconciliationIssue(
                    symbol=symbol,
                    issue_type="AVG_PRICE_MISMATCH",
                    internal_value=str(round(ip.avg_price, 4)),
                    broker_value=str(round(bp.avg_price, 4)),
                )
            )

    return issues

