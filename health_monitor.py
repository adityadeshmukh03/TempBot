from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import perf_counter
from typing import Deque


@dataclass
class HealthMonitor:
    max_latency_ms: int = 2500
    api_failure_threshold: int = 5
    failure_window_seconds: int = 120
    logger: object = None
    latency_ms: float = 0.0
    last_tick_at: datetime | None = None
    trading_disabled_until: datetime | None = None
    failures: dict[str, Deque[datetime]] = field(default_factory=lambda: defaultdict(deque))

    def mark_tick(self):
        self.last_tick_at = datetime.now()

    def timer(self):
        return perf_counter()

    def record_latency(self, started_at: float, label: str = "signal") -> float:
        self.latency_ms = round((perf_counter() - started_at) * 1000, 2)
        if self.logger:
            level = "warning" if self.latency_ms > self.max_latency_ms else "info"
            getattr(self.logger, level)("%s latency=%sms", label, self.latency_ms)
        return self.latency_ms

    def latency_allows_entry(self) -> bool:
        return self.latency_ms <= self.max_latency_ms

    def record_api_failure(self, source: str, exc: Exception | str):
        now = datetime.now()
        bucket = self.failures[source]
        bucket.append(now)
        cutoff = now - timedelta(seconds=self.failure_window_seconds)
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if self.logger:
            self.logger.error("%s failure %s/%s: %s", source, len(bucket), self.api_failure_threshold, exc)
        if len(bucket) >= self.api_failure_threshold:
            self.trading_disabled_until = now + timedelta(seconds=self.failure_window_seconds)
            if self.logger:
                self.logger.critical("trading disabled after %s %s failures", len(bucket), source)

    def trading_allowed(self) -> tuple[bool, str]:
        if self.trading_disabled_until and datetime.now() < self.trading_disabled_until:
            return False, f"API circuit active until {self.trading_disabled_until.strftime('%H:%M:%S')}"
        return True, ""

    def snapshot(self) -> dict:
        return {
            "latency_ms": self.latency_ms,
            "last_tick_at": self.last_tick_at.isoformat(timespec="seconds") if self.last_tick_at else None,
            "trading_disabled_until": (
                self.trading_disabled_until.isoformat(timespec="seconds") if self.trading_disabled_until else None
            ),
            "failures": {source: len(items) for source, items in self.failures.items()},
        }

