from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List

from app.services.liquidity_trap_detector import TrapInput, detect_liquidity_trap
from app.services.ranking_persistence import RankingRepository, StrikeScoreRecord
from app.services.scoring_engine import ScoringInput, compute_confidence_score
from app.services.top5_finder import RankedStrike, top5_ce_pe


@dataclass(frozen=True)
class W03AggregateRow:
    instrument: str
    expiry: str
    symbol: str
    strike: float
    option_type: str  # CE / PE
    technical_score: float
    volume_score: float
    oi_score: float
    greeks_score: float
    liquidity_score: float
    volume: float
    oi_change: float
    atm_distance: float
    oi_cluster_strength: float
    volume_spike_ratio: float
    price_sweep_strength: float
    gamma_exposure: float
    price_direction_up: bool


@dataclass(frozen=True)
class LiquidityAlert:
    instrument: str
    expiry: str
    strike: float
    trap_type: str
    confidence: float
    reason: str
    cycle_ts: datetime


@dataclass(frozen=True)
class W04CycleOutput:
    top5_ce: List[RankedStrike]
    top5_pe: List[RankedStrike]
    alerts: List[LiquidityAlert]
    persisted_records: int


def run_w04_cycle(
    rows: Iterable[W03AggregateRow],
    ranking_repo: RankingRepository,
    cycle_ts: datetime,
) -> W04CycleOutput:
    ranked_items: List[RankedStrike] = []
    persist_rows: List[StrikeScoreRecord] = []
    alerts: List[LiquidityAlert] = []

    for row in rows:
        score = compute_confidence_score(
            ScoringInput(
                technical_score=row.technical_score,
                volume_score=row.volume_score,
                oi_score=row.oi_score,
                greeks_score=row.greeks_score,
                liquidity_score=row.liquidity_score,
            )
        )
        ranked = RankedStrike(
            symbol=row.symbol,
            strike=row.strike,
            option_type=row.option_type,
            confidence_score=score.confidence_score,
            volume=row.volume,
            oi_change=row.oi_change,
            atm_distance=row.atm_distance,
        )
        ranked_items.append(ranked)

        trap = detect_liquidity_trap(
            TrapInput(
                oi_cluster_strength=row.oi_cluster_strength,
                volume_spike_ratio=row.volume_spike_ratio,
                price_sweep_strength=row.price_sweep_strength,
                gamma_exposure=row.gamma_exposure,
                price_direction_up=row.price_direction_up,
            )
        )
        if trap.trap_type != "NONE":
            alerts.append(
                LiquidityAlert(
                    instrument=row.instrument,
                    expiry=row.expiry,
                    strike=row.strike,
                    trap_type=trap.trap_type,
                    confidence=trap.confidence,
                    reason=trap.reason,
                    cycle_ts=cycle_ts,
                )
            )

    top = top5_ce_pe(ranked_items)

    rank_counter = {"CE": 1, "PE": 1}
    top_set = {(x.symbol, x.strike, x.option_type) for x in top["CE"] + top["PE"]}
    for row in rows:
        score = compute_confidence_score(
            ScoringInput(
                technical_score=row.technical_score,
                volume_score=row.volume_score,
                oi_score=row.oi_score,
                greeks_score=row.greeks_score,
                liquidity_score=row.liquidity_score,
            )
        )
        key = (row.symbol, row.strike, row.option_type)
        rank_value = rank_counter[row.option_type.upper()] if key in top_set else 999
        if key in top_set:
            rank_counter[row.option_type.upper()] += 1

        persist_rows.append(
            StrikeScoreRecord(
                instrument=row.instrument,
                expiry=row.expiry,
                strike=row.strike,
                option_type=row.option_type,
                confidence_score=score.confidence_score,
                technical_score=row.technical_score,
                volume_score=row.volume_score,
                oi_score=row.oi_score,
                greeks_score=row.greeks_score,
                liquidity_score=row.liquidity_score,
                rank_value=rank_value,
                cycle_ts=cycle_ts,
                model_version="v1",
            )
        )

    persisted = ranking_repo.save_scores(persist_rows)
    return W04CycleOutput(
        top5_ce=top["CE"],
        top5_pe=top["PE"],
        alerts=alerts,
        persisted_records=persisted,
    )

