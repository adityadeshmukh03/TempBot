from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any


@dataclass
class TradingEngineSnapshot:
    token: int | None
    symbol: str | None
    current_instrument: dict[str, Any] | None
    current_direction: str | None
    subscribed_token: int | None
    ws: Any
    last_analysis_candle: Any
    prev_iv: float | None
    last_oi_result: dict[str, Any] | None
    last_spot_price: float | None
    last_reconcile_at: float
    option_ltp: float


class TradingEngineState:
    def __init__(self):
        self._lock = RLock()
        self.token: int | None = None
        self.symbol: str | None = None
        self.current_instrument: dict[str, Any] | None = None
        self.current_direction: str | None = None
        self.subscribed_token: int | None = None
        self.ws: Any = None
        self.last_analysis_candle: Any = None
        self.prev_iv: float | None = None
        self.last_oi_result: dict[str, Any] | None = None
        self.last_spot_price: float | None = None
        self.last_reconcile_at: float = 0
        self.option_ltp: float = 0

    def snapshot(self) -> TradingEngineSnapshot:
        with self._lock:
            return TradingEngineSnapshot(
                token=self.token,
                symbol=self.symbol,
                current_instrument=dict(self.current_instrument) if self.current_instrument else None,
                current_direction=self.current_direction,
                subscribed_token=self.subscribed_token,
                ws=self.ws,
                last_analysis_candle=self.last_analysis_candle,
                prev_iv=self.prev_iv,
                last_oi_result=dict(self.last_oi_result) if self.last_oi_result else None,
                last_spot_price=self.last_spot_price,
                last_reconcile_at=self.last_reconcile_at,
                option_ltp=self.option_ltp,
            )

    def set_ws(self, ws):
        with self._lock:
            self.ws = ws

    def set_spot(self, spot_price: float | None):
        with self._lock:
            self.last_spot_price = spot_price

    def set_option_ltp(self, ltp: float):
        with self._lock:
            self.option_ltp = ltp

    def set_analysis_candle(self, candle) -> bool:
        with self._lock:
            if candle == self.last_analysis_candle:
                return False
            self.last_analysis_candle = candle
            return True

    def set_reconcile_time(self, ts: float):
        with self._lock:
            self.last_reconcile_at = ts

    def set_oi_result(self, result: dict[str, Any] | None):
        with self._lock:
            self.last_oi_result = result

    def set_prev_iv(self, iv: float | None):
        with self._lock:
            self.prev_iv = iv

    def set_instrument(self, instrument: dict[str, Any], direction: str | None = None):
        with self._lock:
            self.token = instrument.get("instrument_token")
            self.symbol = instrument.get("tradingsymbol")
            self.current_instrument = dict(instrument)
            self.current_direction = direction or instrument.get("direction")

    def set_subscribed_token(self, token: int | None):
        with self._lock:
            self.subscribed_token = token

