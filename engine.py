from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EngineState:
    current_token: int | None = None
    current_symbol: str | None = None
    current_direction: str | None = None
    subscribed_token: int | None = None
    last_spot_price: float | None = None


class TradingEngine:
    def __init__(self, deps):
        self.deps = deps
        self.state = EngineState()

    def set_instrument(self, instrument: dict):
        self.state.current_token = instrument.get("instrument_token")
        self.state.current_symbol = instrument.get("tradingsymbol")
        self.state.current_direction = instrument.get("direction")

