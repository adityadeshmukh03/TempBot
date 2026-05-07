from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections import defaultdict
from typing import Any, Callable


@dataclass
class Event:
    ts: datetime
    payload: dict[str, Any]


class TickEvent(Event):
    pass


class CandleCloseEvent(Event):
    pass


class SignalEvent(Event):
    pass


class EntryEvent(Event):
    pass


class OrderFilledEvent(Event):
    pass


class ExitEvent(Event):
    pass


class ReconnectEvent(Event):
    pass


class ApiFailureEvent(Event):
    pass


class LatencyEvent(Event):
    pass


class EventBus:
    def __init__(self, logger=None):
        self._handlers: dict[type[Event], list[Callable[[Event], None]]] = defaultdict(list)
        self.logger = logger

    def subscribe(self, event_type: type[Event], handler: Callable[[Event], None]):
        self._handlers[event_type].append(handler)

    def publish(self, event: Event):
        for event_type, handlers in list(self._handlers.items()):
            if isinstance(event, event_type):
                for handler in handlers:
                    try:
                        handler(event)
                    except Exception as exc:
                        if self.logger:
                            self.logger.exception("event handler failed for %s: %s", type(event).__name__, exc)
