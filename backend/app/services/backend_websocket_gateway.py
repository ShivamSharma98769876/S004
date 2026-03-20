from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict


@dataclass(frozen=True)
class WsIdentity:
    user_id: int
    role: str


CHANNEL_ACL = {
    "/ws/market": {"admin", "trader"},
    "/ws/strategy-signals": {"admin", "trader"},
    "/ws/trade-updates": {"admin", "trader"},
    "/ws/liquidity-alerts": {"admin", "trader"},
    "/ws/strike-rankings": {"admin", "trader"},
    "/ws/internal-admin": {"admin"},
}


def authenticate_ws_token(token: str | None) -> WsIdentity | None:
    """
    Placeholder auth.
    Replace with JWT verification and permission claims extraction.
    """
    if not token:
        return None
    if token.startswith("admin:"):
        return WsIdentity(user_id=1, role="admin")
    if token.startswith("trader:"):
        return WsIdentity(user_id=2, role="trader")
    return None


def authorize_channel(identity: WsIdentity, channel: str) -> bool:
    allowed_roles = CHANNEL_ACL.get(channel, set())
    return identity.role in allowed_roles


def build_event(event_name: str, correlation_id: str, data: dict, version: str = "1.0") -> str:
    payload = {
        "event_id": f"evt-{int(datetime.utcnow().timestamp() * 1000)}",
        "event_name": event_name,
        "event_version": version,
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "correlation_id": correlation_id,
        "data": data,
    }
    return json.dumps(payload)


class WsGateway:
    """
    Framework-agnostic websocket gateway scaffold for W07-S02.
    """

    def __init__(self) -> None:
        self.subscribers: Dict[str, list[Callable[[str], None]]] = {}

    def subscribe(self, channel: str, callback: Callable[[str], None]) -> None:
        self.subscribers.setdefault(channel, []).append(callback)

    def publish(self, channel: str, message: str) -> None:
        for cb in self.subscribers.get(channel, []):
            cb(message)

