from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal


RiskProfile = Literal["LOW", "MEDIUM", "HIGH"]
PublishStatus = Literal["DRAFT", "PUBLISHED", "ARCHIVED"]


@dataclass(frozen=True)
class StrategyCatalogEntry:
    strategy_id: str
    version: str
    display_name: str
    description: str
    risk_profile: RiskProfile
    supported_segments: List[str]
    owner_type: str
    publish_status: PublishStatus
    performance_snapshot: Dict[str, float]


@dataclass(frozen=True)
class StrategySearchQuery:
    search_text: str = ""
    risk_profile: RiskProfile | None = None
    segment: str | None = None
    published_only: bool = True

