from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from websocket import WebSocketApp


logger = logging.getLogger(__name__)


@dataclass
class ConnectorConfig:
    url: str
    max_retries: int = 20
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 20.0
    heartbeat_timeout_seconds: int = 30


class MarketDataConnector:
    """
    Broker websocket connector with reconnect and jittered backoff.

    This is a reusable foundation module for W02-S01. Integration with a broker
    SDK auth flow should provide tokenized URL and subscription payloads.
    """

    def __init__(
        self,
        config: ConnectorConfig,
        on_tick: Callable[[dict], None],
        subscription_builder: Callable[[], dict],
    ) -> None:
        self.config = config
        self.on_tick = on_tick
        self.subscription_builder = subscription_builder
        self._should_run = False
        self._last_message_ts = 0.0

    def start(self) -> None:
        self._should_run = True
        retries = 0
        while self._should_run:
            try:
                self._run_once()
                retries = 0
            except Exception:
                retries += 1
                if retries > self.config.max_retries:
                    raise
                sleep_for = min(
                    self.config.max_backoff_seconds,
                    self.config.base_backoff_seconds * (2 ** min(retries, 8)),
                )
                sleep_for += random.uniform(0, 0.35 * sleep_for)
                logger.warning("Market data reconnect retry=%s sleep=%.2fs", retries, sleep_for)
                time.sleep(sleep_for)

    def stop(self) -> None:
        self._should_run = False

    def _run_once(self) -> None:
        self._last_message_ts = time.time()

        def on_open(ws: WebSocketApp) -> None:
            payload = self.subscription_builder()
            ws.send(json.dumps(payload))

        def on_message(_: WebSocketApp, message: str) -> None:
            self._last_message_ts = time.time()
            tick = json.loads(message)
            self.on_tick(tick)

        def on_error(_: WebSocketApp, error: Exception) -> None:
            logger.warning("Market data websocket error: %s", error)

        ws = WebSocketApp(
            self.config.url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
        )

        # run_forever returns when socket closes or errors.
        ws.run_forever(ping_interval=20, ping_timeout=10)

        if not self._should_run:
            return

        # stale feed guard
        if (time.time() - self._last_message_ts) > self.config.heartbeat_timeout_seconds:
            raise TimeoutError("Feed heartbeat timeout exceeded")

