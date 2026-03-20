from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class BrokerAdapter(Protocol):
    def place_buy(self, symbol: str, quantity: int, expected_price: float) -> tuple[str, float]: ...


@dataclass(frozen=True)
class ExecutionRequest:
    user_id: int
    recommendation_id: str
    symbol: str
    quantity: int
    expected_price: float
    mode: str  # PAPER / LIVE


@dataclass(frozen=True)
class ExecutionResponse:
    success: bool
    mode: str
    broker_order_id: str
    fill_price: float
    reason_code: str
    message: str


class PaperBrokerAdapter:
    def place_buy(self, symbol: str, quantity: int, expected_price: float) -> tuple[str, float]:
        order_id = f"PAPER-{symbol}-{quantity}"
        return order_id, expected_price


class LiveBrokerAdapter:
    """
    Placeholder live adapter.
    Replace this with actual broker integration in production.
    """

    def place_buy(self, symbol: str, quantity: int, expected_price: float) -> tuple[str, float]:
        order_id = f"LIVE-{symbol}-{quantity}"
        fill_price = expected_price
        return order_id, fill_price


def execute_order(req: ExecutionRequest, live_adapter: BrokerAdapter | None = None) -> ExecutionResponse:
    if req.mode == "PAPER":
        adapter = PaperBrokerAdapter()
    elif req.mode == "LIVE":
        adapter = live_adapter or LiveBrokerAdapter()
    else:
        return ExecutionResponse(False, req.mode, "", 0.0, "INVALID_MODE", "Unsupported execution mode")

    order_id, fill_price = adapter.place_buy(req.symbol, req.quantity, req.expected_price)
    return ExecutionResponse(
        success=True,
        mode=req.mode,
        broker_order_id=order_id,
        fill_price=fill_price,
        reason_code="ORDER_PLACED",
        message="Order executed successfully",
    )

