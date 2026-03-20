from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json

try:
    from ..W05.recommendation_engine import TradeRecommendation
except ImportError:  # pragma: no cover
    from recommendation_engine import TradeRecommendation  # type: ignore

try:
    from .execution_adapter import ExecutionRequest, ExecutionResponse, execute_order
    from .execution_repository import ExecutionRepository, LiveTradeRow, TradeEventRow
    from .execution_state_machine import TradeState, transition_state
    from .risk_gates import RiskContext, RiskDecision, RiskLimits, evaluate_post_trade_risk, evaluate_pre_trade_risk
except ImportError:  # pragma: no cover
    from execution_adapter import ExecutionRequest, ExecutionResponse, execute_order
    from execution_repository import ExecutionRepository, LiveTradeRow, TradeEventRow
    from execution_state_machine import TradeState, transition_state
    from risk_gates import RiskContext, RiskDecision, RiskLimits, evaluate_post_trade_risk, evaluate_pre_trade_risk


@dataclass(frozen=True)
class AcceptanceRequest:
    recommendation: TradeRecommendation
    quantity: int
    mode: str  # PAPER / LIVE
    risk_limits: RiskLimits
    risk_context: RiskContext
    execution_repo: ExecutionRepository | None = None


@dataclass(frozen=True)
class AcceptanceResult:
    accepted: bool
    trade_ref: str
    final_state: str
    reason_code: str
    message: str
    execution: ExecutionResponse | None


def accept_recommendation_and_execute(req: AcceptanceRequest) -> AcceptanceResult:
    now = datetime.utcnow()
    if req.recommendation.status != "GENERATED":
        return AcceptanceResult(
            accepted=False,
            trade_ref="",
            final_state=TradeState.REJECTED.value,
            reason_code="RECOMMENDATION_NOT_ACTIONABLE",
            message="Only GENERATED recommendations can be accepted",
            execution=None,
        )

    pre = evaluate_pre_trade_risk(req.risk_limits, req.risk_context)
    if not pre.allowed:
        if req.execution_repo:
            req.execution_repo.insert_event(
                TradeEventRow(
                    trade_ref=f"TRD-{req.recommendation.recommendation_id}",
                    event_type="RISK_PRECHECK_FAILED",
                    prev_state=TradeState.ENTRY.value,
                    next_state=TradeState.REJECTED.value,
                    reason_code=pre.reason_code,
                    event_payload_json=json.dumps({"message": pre.message}),
                    occurred_at=now,
                )
            )
        return AcceptanceResult(
            accepted=False,
            trade_ref="",
            final_state=TradeState.REJECTED.value,
            reason_code=pre.reason_code,
            message=pre.message,
            execution=None,
        )

    entry_transition = transition_state(TradeState.ENTRY, TradeState.ACTIVE, at=now)
    if not entry_transition.allowed:
        return AcceptanceResult(
            accepted=False,
            trade_ref="",
            final_state=TradeState.REJECTED.value,
            reason_code="STATE_TRANSITION_FAILED",
            message="ENTRY->ACTIVE transition failed",
            execution=None,
        )

    trade_ref = f"TRD-{req.recommendation.recommendation_id}"
    exec_req = ExecutionRequest(
        user_id=req.recommendation.user_id,
        recommendation_id=req.recommendation.recommendation_id,
        symbol=req.recommendation.symbol,
        quantity=req.quantity,
        expected_price=req.recommendation.entry_price,
        mode=req.mode,
    )
    exec_res = execute_order(exec_req)
    if not exec_res.success:
        if req.execution_repo:
            req.execution_repo.insert_event(
                TradeEventRow(
                    trade_ref=trade_ref,
                    event_type="ORDER_REJECTED",
                    prev_state=TradeState.ENTRY.value,
                    next_state=TradeState.REJECTED.value,
                    reason_code=exec_res.reason_code,
                    event_payload_json=json.dumps({"message": exec_res.message}),
                    occurred_at=now,
                )
            )
        return AcceptanceResult(
            accepted=False,
            trade_ref=trade_ref,
            final_state=TradeState.REJECTED.value,
            reason_code=exec_res.reason_code,
            message=exec_res.message,
            execution=exec_res,
        )

    post = evaluate_post_trade_risk(
        fill_price=exec_res.fill_price,
        expected_price=req.recommendation.entry_price,
        max_slippage_pct=req.risk_limits.max_slippage_pct,
    )
    if not post.allowed:
        exit_transition = transition_state(TradeState.ACTIVE, TradeState.EXIT, at=now)
        final_state = TradeState.EXIT.value if exit_transition.allowed else TradeState.ACTIVE.value
        if req.execution_repo:
            req.execution_repo.upsert_live_trade(
                LiveTradeRow(
                    trade_ref=trade_ref,
                    recommendation_id=req.recommendation.recommendation_id,
                    user_id=req.recommendation.user_id,
                    strategy_id=req.recommendation.strategy_id,
                    symbol=req.recommendation.symbol,
                    mode=req.mode,
                    quantity=req.quantity,
                    entry_price=req.recommendation.entry_price,
                    target_price=req.recommendation.target_price,
                    stop_loss_price=req.recommendation.stop_loss_price,
                    current_state=final_state,
                    broker_order_id=exec_res.broker_order_id,
                    created_at=now,
                )
            )
            req.execution_repo.insert_event(
                TradeEventRow(
                    trade_ref=trade_ref,
                    event_type="RISK_POSTCHECK_FAILED",
                    prev_state=TradeState.ACTIVE.value,
                    next_state=final_state,
                    reason_code=post.reason_code,
                    event_payload_json=json.dumps({"message": post.message}),
                    occurred_at=now,
                )
            )
        return AcceptanceResult(
            accepted=False,
            trade_ref=trade_ref,
            final_state=final_state,
            reason_code=post.reason_code,
            message=post.message,
            execution=exec_res,
        )

    if req.execution_repo:
        req.execution_repo.upsert_live_trade(
            LiveTradeRow(
                trade_ref=trade_ref,
                recommendation_id=req.recommendation.recommendation_id,
                user_id=req.recommendation.user_id,
                strategy_id=req.recommendation.strategy_id,
                symbol=req.recommendation.symbol,
                mode=req.mode,
                quantity=req.quantity,
                entry_price=req.recommendation.entry_price,
                target_price=req.recommendation.target_price,
                stop_loss_price=req.recommendation.stop_loss_price,
                current_state=TradeState.ACTIVE.value,
                broker_order_id=exec_res.broker_order_id,
                created_at=now,
            )
        )
        req.execution_repo.insert_event(
            TradeEventRow(
                trade_ref=trade_ref,
                event_type="ORDER_EXECUTED",
                prev_state=TradeState.ENTRY.value,
                next_state=TradeState.ACTIVE.value,
                reason_code=exec_res.reason_code,
                event_payload_json=json.dumps(
                    {
                        "fill_price": exec_res.fill_price,
                        "mode": req.mode,
                    }
                ),
                occurred_at=now,
            )
        )

    return AcceptanceResult(
        accepted=True,
        trade_ref=trade_ref,
        final_state=TradeState.ACTIVE.value,
        reason_code="EXECUTION_ACCEPTED",
        message="Recommendation accepted and order executed",
        execution=exec_res,
    )

