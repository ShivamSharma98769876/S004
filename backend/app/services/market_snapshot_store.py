from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict


@dataclass
class SnapshotConfig:
    raw_tick_ttl_seconds: int = 3600
    latest_snapshot_ttl_seconds: int = 120


class InMemorySnapshotStore:
    """
    Snapshot store scaffold for W02-S04.

    Replace internals with Redis operations in production:
    - latest:{symbol}
    - rawticks:{symbol}:{minute_bucket}
    """

    def __init__(self, config: SnapshotConfig | None = None) -> None:
        self.config = config or SnapshotConfig()
        self._latest: Dict[str, Dict[str, Any]] = {}
        self._raw: Dict[str, list[Dict[str, Any]]] = {}

    def save_latest(self, symbol: str, payload: Dict[str, Any]) -> None:
        self._latest[symbol] = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }

    def append_raw_tick(self, symbol: str, tick: Dict[str, Any]) -> None:
        self._raw.setdefault(symbol, []).append(
            {"saved_at": datetime.now(timezone.utc).isoformat(), "tick": tick}
        )

    def get_latest(self, symbol: str) -> Dict[str, Any] | None:
        item = self._latest.get(symbol)
        if not item:
            return None
        return item["payload"]

    def raw_count(self, symbol: str) -> int:
        return len(self._raw.get(symbol, []))

