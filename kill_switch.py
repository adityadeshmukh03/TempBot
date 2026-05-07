from __future__ import annotations


class KillSwitch:
    def __init__(self, max_consecutive_losses=3, max_latency_ms=2500,
                 max_slippage_pct=2.0, api_failure_threshold=5):
        self.max_consecutive_losses = max_consecutive_losses
        self.max_latency_ms = max_latency_ms
        self.max_slippage_pct = max_slippage_pct
        self.api_failure_threshold = api_failure_threshold
        self.consecutive_losses = 0
        self.api_failures = 0
        self.halted_reason = ""

    def record_outcome(self, outcome: str):
        self.consecutive_losses = self.consecutive_losses + 1 if outcome == "LOSS" else 0

    def record_api_failure(self):
        self.api_failures += 1

    def check(self, latency_ms=None, slippage_pct=None, spread_pct=None) -> tuple[bool, str]:
        if self.consecutive_losses >= self.max_consecutive_losses:
            return True, "max consecutive losses reached"
        if self.api_failures >= self.api_failure_threshold:
            return True, "API failure threshold reached"
        if latency_ms is not None and latency_ms > self.max_latency_ms:
            return True, "latency threshold breached"
        if slippage_pct is not None and slippage_pct > self.max_slippage_pct:
            return True, "slippage threshold breached"
        if spread_pct is not None and spread_pct > self.max_slippage_pct:
            return True, "spread explosion halt"
        return False, ""

