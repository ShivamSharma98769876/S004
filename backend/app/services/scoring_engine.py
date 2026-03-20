from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringInput:
    technical_score: float
    volume_score: float
    oi_score: float
    greeks_score: float
    liquidity_score: float


@dataclass(frozen=True)
class ScoreWeights:
    technical: float = 1.0
    volume: float = 1.0
    oi: float = 1.0
    greeks: float = 1.0
    liquidity: float = 1.0


@dataclass(frozen=True)
class ScoringOutput:
    confidence_score: float
    factor_breakdown: dict[str, float]


def compute_confidence_score(inp: ScoringInput, w: ScoreWeights | None = None) -> ScoringOutput:
    weights = w or ScoreWeights()
    weighted = {
        "technical": inp.technical_score * weights.technical,
        "volume": inp.volume_score * weights.volume,
        "oi": inp.oi_score * weights.oi,
        "greeks": inp.greeks_score * weights.greeks,
        "liquidity": inp.liquidity_score * weights.liquidity,
    }
    total = sum(weighted.values())
    return ScoringOutput(confidence_score=round(total, 4), factor_breakdown=weighted)

