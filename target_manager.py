from __future__ import annotations


class TargetManager:
    def __init__(self):
        self.targets = {}

    def register(self, symbol: str, target_1: float, target_2: float, quantity: int,
                 partial_exit_pct: float = 0.5):
        qty1 = max(1, int(quantity * partial_exit_pct))
        qty2 = max(quantity - qty1, 0)
        self.targets[symbol] = {
            "target_1": float(target_1),
            "target_2": float(target_2),
            "qty1": qty1,
            "qty2": qty2,
            "t1_done": False,
        }
        return self.targets[symbol]

    def exit_quantity(self, symbol: str, ltp: float) -> tuple[int, str]:
        item = self.targets.get(symbol)
        if not item:
            return 0, ""
        if not item["t1_done"] and ltp >= item["target_1"]:
            item["t1_done"] = True
            return item["qty1"], "TARGET_1"
        if item["qty2"] and ltp >= item["target_2"]:
            return item["qty2"], "TARGET_2"
        return 0, ""

    def clear(self, symbol: str):
        return self.targets.pop(symbol, None)

