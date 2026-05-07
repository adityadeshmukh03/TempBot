from __future__ import annotations


class TrailingStopManager:
    def __init__(self, enabled: bool = True, trail_after_r: float = 1.0, trail_by_r: float = 0.5):
        self.enabled = enabled
        self.trail_after_r = trail_after_r
        self.trail_by_r = trail_by_r
        self.state = {}

    def register(self, symbol: str, entry: float, stop_loss: float):
        risk = max(entry - stop_loss, 0.01)
        self.state[symbol] = {"entry": entry, "stop_loss": stop_loss, "risk": risk}

    def update(self, symbol: str, ltp: float) -> float | None:
        item = self.state.get(symbol)
        if not self.enabled or not item:
            return None
        profit_r = (ltp - item["entry"]) / item["risk"]
        if profit_r < self.trail_after_r:
            return item["stop_loss"]
        new_sl = max(item["stop_loss"], ltp - item["risk"] * self.trail_by_r)
        item["stop_loss"] = round(new_sl, 2)
        return item["stop_loss"]

    def clear(self, symbol: str):
        return self.state.pop(symbol, None)

