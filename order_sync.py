from __future__ import annotations


class OrderSynchronizer:
    def __init__(self, kite, logger=None):
        self.kite = kite
        self.logger = logger

    def broker_positions(self) -> list[dict]:
        try:
            positions = self.kite.positions()
            return positions.get("net", []) if isinstance(positions, dict) else []
        except Exception as exc:
            if self.logger:
                self.logger.exception("broker position fetch failed: %s", exc)
            return []

    def reconcile_symbol(self, symbol: str, expected_qty: int) -> dict:
        actual = 0
        for row in self.broker_positions():
            if row.get("tradingsymbol") == symbol:
                actual += int(row.get("quantity") or 0)
        return {
            "symbol": symbol,
            "expected_qty": expected_qty,
            "actual_qty": actual,
            "in_sync": int(expected_qty) == actual,
        }
