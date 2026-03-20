from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class RankedStrike:
    symbol: str
    strike: float
    option_type: str  # CE / PE
    confidence_score: float
    volume: float
    oi_change: float
    atm_distance: float


def _sort_key(item: RankedStrike) -> tuple[float, float, float, float]:
    return (
        item.confidence_score,
        item.volume,
        item.oi_change,
        -abs(item.atm_distance),
    )


def top5_by_option_type(items: Iterable[RankedStrike], option_type: str) -> List[RankedStrike]:
    subset = [x for x in items if x.option_type.upper() == option_type.upper()]
    subset.sort(key=_sort_key, reverse=True)
    return subset[:5]


def top5_ce_pe(items: Iterable[RankedStrike]) -> dict[str, List[RankedStrike]]:
    all_items = list(items)
    return {
        "CE": top5_by_option_type(all_items, "CE"),
        "PE": top5_by_option_type(all_items, "PE"),
    }

