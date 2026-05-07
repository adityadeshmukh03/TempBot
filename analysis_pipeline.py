from __future__ import annotations


class AnalysisPipeline:
    def __init__(self, steps=None):
        self.steps = steps or []

    def run(self, context: dict) -> dict:
        for step in self.steps:
            context = step(context)
        return context

