from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class LiveModeContext:
    broker_connected: bool
    has_api_key: bool
    has_access_token: bool
    risk_limits_configured: bool
    previous_mode_change_at: datetime | None
    requested_at: datetime
    cooling_period_minutes: int = 10


@dataclass(frozen=True)
class GuardrailDecision:
    allowed: bool
    reason_code: str
    message: str


def evaluate_live_mode_guardrails(ctx: LiveModeContext) -> GuardrailDecision:
    if not ctx.broker_connected:
        return GuardrailDecision(False, "BROKER_NOT_CONNECTED", "Broker must be connected for LIVE mode")
    if not ctx.has_api_key or not ctx.has_access_token:
        return GuardrailDecision(False, "BROKER_CREDENTIALS_MISSING", "API key/access token missing")
    if not ctx.risk_limits_configured:
        return GuardrailDecision(False, "RISK_LIMITS_NOT_CONFIGURED", "Configure risk limits before LIVE mode")

    if ctx.previous_mode_change_at is not None:
        elapsed = ctx.requested_at - ctx.previous_mode_change_at
        if elapsed < timedelta(minutes=ctx.cooling_period_minutes):
            return GuardrailDecision(
                False,
                "MODE_SWITCH_COOLDOWN_ACTIVE",
                f"Wait {ctx.cooling_period_minutes} minutes between mode switches",
            )

    return GuardrailDecision(True, "ALLOWED", "LIVE mode prechecks passed")

