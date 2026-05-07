from __future__ import annotations


class RejectionHandler:
    def classify(self, error: str) -> str:
        text = (error or "").lower()
        if "margin" in text or "fund" in text:
            return "MARGIN"
        if "price" in text or "trigger" in text:
            return "PRICE"
        if "network" in text or "timeout" in text:
            return "TRANSIENT"
        return "UNKNOWN"

    def can_retry(self, error: str) -> bool:
        return self.classify(error) in {"PRICE", "TRANSIENT"}

    def recovery_note(self, error: str) -> str:
        kind = self.classify(error)
        if kind == "MARGIN":
            return "Reduce lots or halt entries until funds are checked."
        if kind == "PRICE":
            return "Refresh quote and retry with MARKET or wider limit."
        if kind == "TRANSIENT":
            return "Retry after broker/network recovery."
        return "Manual review required."

