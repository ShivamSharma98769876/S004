from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List


@dataclass(frozen=True)
class AlertEvent:
    alert_id: str
    severity: str  # INFO / WARN / CRITICAL
    category: str  # LIQUIDITY_TRAP / EXECUTION_FAILURE / RISK_BREACH
    message: str
    occurred_at: datetime
    correlation_id: str


@dataclass(frozen=True)
class AlertDeliveryResult:
    channel: str  # IN_APP / TELEGRAM / EMAIL / SMS
    success: bool
    attempt_no: int
    reason: str


class AlertEscalationPolicy:
    """
    Retry + escalation flow scaffold for W07-S04.
    """

    def __init__(self) -> None:
        self.channel_order = ["IN_APP", "TELEGRAM", "EMAIL"]
        self.max_attempts_per_channel = 3
        self.retry_backoff_seconds = [1, 3, 10]

    def target_channels(self, event: AlertEvent) -> List[str]:
        if event.severity == "CRITICAL":
            return self.channel_order
        if event.severity == "WARN":
            return ["IN_APP", "TELEGRAM"]
        return ["IN_APP"]


class AlertDeliveryEngine:
    """
    Non-networked delivery simulator.
    Replace `_send` with actual integrations.
    """

    def __init__(self, policy: AlertEscalationPolicy | None = None) -> None:
        self.policy = policy or AlertEscalationPolicy()

    def dispatch(self, event: AlertEvent) -> Dict[str, List[AlertDeliveryResult]]:
        results: Dict[str, List[AlertDeliveryResult]] = {}
        for channel in self.policy.target_channels(event):
            channel_results: List[AlertDeliveryResult] = []
            for attempt in range(1, self.policy.max_attempts_per_channel + 1):
                ok, reason = self._send(channel, event, attempt)
                channel_results.append(
                    AlertDeliveryResult(
                        channel=channel,
                        success=ok,
                        attempt_no=attempt,
                        reason=reason,
                    )
                )
                if ok:
                    break
            results[channel] = channel_results
        return results

    def _send(self, channel: str, event: AlertEvent, attempt: int) -> tuple[bool, str]:
        # Deterministic scaffold behavior: first attempt on TELEGRAM can fail for retry simulation.
        if channel == "TELEGRAM" and event.severity == "CRITICAL" and attempt == 1:
            return False, "Temporary gateway timeout"
        return True, "Delivered"

