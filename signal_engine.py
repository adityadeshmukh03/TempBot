from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


class SignalEngine:
    def __init__(self, kite, config, logger=None, health_monitor=None):
        self.kite = kite
        self.config = config
        self.logger = logger
        self.health = health_monitor

    def fetch_market_inputs(self, spot_price, underlying_token, run_oi_analysis, get_market_context):
        with ThreadPoolExecutor(max_workers=2) as executor:
            oi_future = executor.submit(
                run_oi_analysis,
                self.kite,
                underlying_name=self.config.INSTRUMENT,
                spot_price=spot_price,
            )
            context_future = executor.submit(
                get_market_context,
                self.kite,
                underlying_token=underlying_token,
                spot_price=spot_price,
            )
            return oi_future.result(), context_future.result()

    def deterministic_fallback_signal(self, confluence: dict, trade_levels: dict, reason: str) -> dict:
        if confluence.get("met"):
            return {
                "signal": "WAIT",
                "confidence": "low",
                "decision_reason": f"Deterministic fallback blocked entry: {reason}",
                "audit_verdict": "VETO",
                "condition": "",
                "downgraded_by": "deterministic_fallback",
                "confluence_score": confluence.get("score", 0),
                "confluence_required": confluence.get("required", 0),
                "confluence_met": False,
                "confluence_reasons": confluence.get("reasons", []),
                "confluence_missed": confluence.get("missed", []),
                "entry_price": trade_levels.get("entry_price", 0),
                "stop_loss": trade_levels.get("stop_loss", 0),
                "target_1": trade_levels.get("target_1", 0),
                "target_2": trade_levels.get("target_2", 0),
                "risk_points": trade_levels.get("risk_points", 0),
                "veto_reason": reason,
                "warn_reasons": "",
                "what_auditor_checked": "Gemini unavailable; deterministic fallback failed closed.",
                "near_resistance": False,
                "resistance_level": 0,
                "liquidity_events": [],
                "audit_flags": ["Deterministic_Fallback"],
            }
        return {}
