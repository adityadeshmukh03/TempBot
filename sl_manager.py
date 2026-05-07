from __future__ import annotations


class StopLossManager:
    def __init__(self):
        self.active = {}

    def register(self, symbol: str, stop_loss: float, quantity: int, order_id: str = ""):
        self.active[symbol] = {
            "stop_loss": float(stop_loss),
            "quantity": int(quantity),
            "order_id": order_id,
        }
        return self.active[symbol]

    def should_exit(self, symbol: str, ltp: float) -> bool:
        item = self.active.get(symbol)
        return bool(item and ltp <= item["stop_loss"])

    def update(self, symbol: str, stop_loss: float):
        item = self.active.get(symbol)
        if not item:
            return None
        item["stop_loss"] = float(stop_loss)
        return item

    def clear(self, symbol: str):
        return self.active.pop(symbol, None)
