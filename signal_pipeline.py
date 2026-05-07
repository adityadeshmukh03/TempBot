from __future__ import annotations


class SignalPipeline:
    def __init__(self, filters=None):
        self.filters = filters or []

    def accept(self, signal: dict) -> tuple[bool, str]:
        for fn in self.filters:
            ok, reason = fn(signal)
            if not ok:
                return False, reason
        return True, ""

