from __future__ import annotations

from datetime import datetime

from events import ReconnectEvent


class TradingEngine:
    def __init__(self, state, event_bus=None, logger=None):
        self.state = state
        self.event_bus = event_bus
        self.logger = logger

    def ensure_subscribed(self, ws, new_token):
        snap = self.state.snapshot()
        if ws is None or new_token is None:
            return
        if new_token != snap.subscribed_token:
            if snap.subscribed_token:
                ws.unsubscribe([snap.subscribed_token])
            ws.subscribe([new_token])
            ws.set_mode(ws.MODE_FULL, [new_token])
            self.state.set_subscribed_token(new_token)

    def on_reconnect(self, attempt: int, reason: str = ""):
        if self.logger:
            self.logger.warning("websocket reconnect attempt=%s reason=%s", attempt, reason)
        if self.event_bus:
            self.event_bus.publish(ReconnectEvent(datetime.now(), {"attempt": attempt, "reason": reason}))
