from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import time


@dataclass
class WebSocketHealth:
    last_tick_at: datetime | None = None
    reconnects: int = 0
    stale: bool = False


class WebSocketWatchdog:
    def __init__(self, stale_after_seconds: int = 15, logger=None):
        self.stale_after = timedelta(seconds=stale_after_seconds)
        self.logger = logger
        self.health = WebSocketHealth()

    def mark_tick(self):
        self.health.last_tick_at = datetime.now()
        self.health.stale = False

    def is_stale(self) -> bool:
        if self.health.last_tick_at is None:
            return True
        self.health.stale = datetime.now() - self.health.last_tick_at > self.stale_after
        return self.health.stale

    def sleep_for_retry(self, attempt: int, base_delay: int, max_delay: int):
        delay = min(base_delay * (2 ** max(attempt - 1, 0)), max_delay)
        if self.logger:
            self.logger.warning("websocket retry sleep=%ss attempt=%s", delay, attempt)
        time.sleep(delay)

    def resubscribe(self, ws, tokens: list[int], mode=None):
        if not ws or not tokens:
            return
        ws.subscribe(tokens)
        if mode is not None:
            ws.set_mode(mode, tokens)

