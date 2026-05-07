from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter


class LatencyMonitor:
    def __init__(self, logger=None):
        self.logger = logger
        self.samples = []

    @contextmanager
    def measure(self, label: str):
        start = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = round((perf_counter() - start) * 1000, 2)
            self.samples.append((label, elapsed_ms))
            if self.logger:
                self.logger.info("latency %s=%sms", label, elapsed_ms)

