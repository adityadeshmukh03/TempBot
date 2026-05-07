from __future__ import annotations

from collections import defaultdict


class FeatureImportanceTracker:
    def __init__(self):
        self.counts = defaultdict(lambda: {"win": 0, "loss": 0})

    def record(self, features: list[str], outcome: str):
        bucket = "win" if outcome == "WIN" else "loss" if outcome == "LOSS" else None
        if not bucket:
            return
        for feature in features:
            self.counts[feature][bucket] += 1

    def summary(self) -> dict:
        out = {}
        for feature, counts in self.counts.items():
            total = counts["win"] + counts["loss"]
            out[feature] = {
                **counts,
                "win_rate": round(counts["win"] / total * 100, 2) if total else 0,
            }
        return out
