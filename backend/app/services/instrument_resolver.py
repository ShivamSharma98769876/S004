from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional


@dataclass(frozen=True)
class InstrumentKey:
    underlying: str
    expiry: str
    strike: int
    option_type: str  # CE / PE


class InstrumentResolver:
    """
    In-memory token resolver scaffold for W02-S02.

    Production implementation should load tokens from broker instruments dump
    into Redis/Postgres and refresh at least daily.
    """

    def __init__(self) -> None:
        self._token_map: Dict[InstrumentKey, int] = {}

    def upsert(
        self,
        underlying: str,
        expiry: str,
        strike: int,
        option_type: str,
        token: int,
    ) -> None:
        key = InstrumentKey(
            underlying=underlying.upper().strip(),
            expiry=expiry.upper().strip(),
            strike=int(strike),
            option_type=option_type.upper().strip(),
        )
        self._token_map[key] = int(token)

    def resolve(
        self,
        underlying: str,
        expiry: str,
        strike: int,
        option_type: str,
    ) -> Optional[int]:
        key = InstrumentKey(
            underlying=underlying.upper().strip(),
            expiry=expiry.upper().strip(),
            strike=int(strike),
            option_type=option_type.upper().strip(),
        )
        return self._token_map.get(key)

    def count(self) -> int:
        return len(self._token_map)

