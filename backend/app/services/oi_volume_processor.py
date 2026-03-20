from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class OiVolumePoint:
    oi: float
    volume: float


@dataclass
class OiVolumeFeatures:
    oi_change_pct: float
    volume_spike_ratio: float


def compute_oi_volume_features(points: List[OiVolumePoint]) -> OiVolumeFeatures:
    if len(points) < 2:
        return OiVolumeFeatures(oi_change_pct=0.0, volume_spike_ratio=0.0)

    prev = points[-2]
    curr = points[-1]

    oi_change_pct = 0.0
    if prev.oi > 0:
        oi_change_pct = ((curr.oi - prev.oi) / prev.oi) * 100.0

    baseline = sum(p.volume for p in points[:-1]) / max(len(points) - 1, 1)
    volume_spike_ratio = (curr.volume / baseline) if baseline > 0 else 0.0

    return OiVolumeFeatures(
        oi_change_pct=round(oi_change_pct, 2),
        volume_spike_ratio=round(volume_spike_ratio, 2),
    )

