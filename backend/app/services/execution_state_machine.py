from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Tuple


class TradeState(str, Enum):
    ENTRY = "ENTRY"
    ACTIVE = "ACTIVE"
    TRAIL = "TRAIL"
    EXIT = "EXIT"
    REJECTED = "REJECTED"


ALLOWED_TRANSITIONS: Dict[TradeState, Tuple[TradeState, ...]] = {
    TradeState.ENTRY: (TradeState.ACTIVE, TradeState.REJECTED, TradeState.EXIT),
    TradeState.ACTIVE: (TradeState.TRAIL, TradeState.EXIT),
    TradeState.TRAIL: (TradeState.EXIT,),
    TradeState.EXIT: (),
    TradeState.REJECTED: (),
}


@dataclass(frozen=True)
class TransitionResult:
    allowed: bool
    reason: str
    previous_state: TradeState
    next_state: TradeState
    at: datetime


def transition_state(current: TradeState, nxt: TradeState, at: datetime | None = None) -> TransitionResult:
    now = at or datetime.utcnow()
    allowed = nxt in ALLOWED_TRANSITIONS[current]
    if allowed:
        return TransitionResult(True, "TRANSITION_ALLOWED", current, nxt, now)
    return TransitionResult(False, "INVALID_TRANSITION", current, current, now)

