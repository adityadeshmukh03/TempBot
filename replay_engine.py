from __future__ import annotations


class ReplayEngine:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def replay_candles(self, candles: list[dict]) -> list[dict]:
        results = []
        for candle in candles:
            results.append(self.pipeline.run({"candle": candle}))
        return results

