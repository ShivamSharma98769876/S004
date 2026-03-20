from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TrapInput:
    oi_cluster_strength: float
    volume_spike_ratio: float
    price_sweep_strength: float
    gamma_exposure: float
    price_direction_up: bool


@dataclass(frozen=True)
class TrapAlert:
    trap_type: str  # BULL_TRAP / BEAR_TRAP / NONE
    confidence: float
    reason: str


def detect_liquidity_trap(inp: TrapInput) -> TrapAlert:
    evidence = 0.0
    evidence += min(max(inp.oi_cluster_strength, 0.0), 10.0) * 0.25
    evidence += min(max(inp.volume_spike_ratio, 0.0), 10.0) * 0.25
    evidence += min(max(inp.price_sweep_strength, 0.0), 10.0) * 0.25
    evidence += min(max(abs(inp.gamma_exposure), 0.0), 10.0) * 0.25
    confidence = round(min(evidence * 10.0, 100.0), 2)

    if confidence < 55:
        return TrapAlert(trap_type="NONE", confidence=confidence, reason="Evidence below threshold")

    if inp.price_direction_up:
        return TrapAlert(
            trap_type="BULL_TRAP",
            confidence=confidence,
            reason="Upward sweep into high-liquidity zone with adverse gamma pressure",
        )

    return TrapAlert(
        trap_type="BEAR_TRAP",
        confidence=confidence,
        reason="Downward sweep into high-liquidity zone with adverse gamma pressure",
    )

