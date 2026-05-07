from __future__ import annotations

from datetime import datetime
from typing import Optional


REGIME_RULES = {
    "Trend Expansion": {
        "supports": ["BULLISH_INTRADAY_MOMENTUM_CONTINUATION", "OPENING_DRIVE_CONTINUATION"],
        "confluence_adjustment": -1,
        "risk_multiplier": 1.0,
    },
    "Mean Reversion": {
        "supports": ["VWAP_RECLAIM_BOUNCE"],
        "confluence_adjustment": 1,
        "risk_multiplier": 0.75,
    },
    "Volatility Compression": {
        "supports": ["VOLATILITY_EXPANSION_BREAKOUT"],
        "confluence_adjustment": 1,
        "risk_multiplier": 0.75,
    },
    "Expiry Chaos": {
        "supports": [],
        "confluence_adjustment": 2,
        "risk_multiplier": 0.5,
    },
    "News Shock": {
        "supports": [],
        "confluence_adjustment": 2,
        "risk_multiplier": 0.5,
    },
    "Liquidity Hunt": {
        "supports": ["LIQUIDITY_SWEEP_REVERSAL"],
        "confluence_adjustment": 1,
        "risk_multiplier": 0.5,
    },
    "Opening Drive": {
        "supports": ["OPENING_DRIVE_CONTINUATION"],
        "confluence_adjustment": 0,
        "risk_multiplier": 0.75,
    },
    "Short Covering Rally": {
        "supports": ["BULLISH_INTRADAY_MOMENTUM_CONTINUATION"],
        "confluence_adjustment": -1,
        "risk_multiplier": 1.0,
    },
    "Mixed": {
        "supports": [],
        "confluence_adjustment": 1,
        "risk_multiplier": 0.75,
    },
}


def _recent_range_ratio(candles: list[dict]) -> float:
    if not candles or len(candles) < 10:
        return 1.0
    ranges = [max(float(c["high"]) - float(c["low"]), 0) for c in candles]
    recent = sum(ranges[-3:]) / 3
    base = sum(ranges[-10:]) / 10
    return recent / base if base > 0 else 1.0


def _alternating_chop(candles: list[dict]) -> bool:
    if len(candles) < 5:
        return False
    dirs = []
    for candle in candles[-5:]:
        close = float(candle.get("close", 0))
        open_ = float(candle.get("open", 0))
        dirs.append(1 if close > open_ else -1 if close < open_ else 0)
    flips = sum(1 for a, b in zip(dirs, dirs[1:]) if a and b and a != b)
    return flips >= 3


def detect_regime(indicators: dict, market_context: Optional[dict] = None,
                  option_data: Optional[dict] = None, oi_result: Optional[dict] = None,
                  recent_candles: Optional[list[dict]] = None,
                  liquidity: Optional[dict] = None,
                  now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    market_context = market_context or {}
    option_data = option_data or {}
    oi_result = oi_result or {}
    recent_candles = recent_candles or []
    liquidity = liquidity or {}

    adx = float(indicators.get("adx") or 0)
    rsi = float(indicators.get("rsi") or 50)
    macd_hist = float(indicators.get("macd_hist") or 0)
    macd_growing = bool(indicators.get("macd_hist_growing"))
    volume_spike = bool(indicators.get("volume_spike"))
    range_ratio = _recent_range_ratio(recent_candles)
    dte = option_data.get("days_to_expiry")
    iv_direction = option_data.get("iv_direction")
    iv_rank = option_data.get("iv_rank")
    opening_bias = market_context.get("opening_range", {}).get("bias")
    alignment = market_context.get("alignment")
    buildup = oi_result.get("oi_buildup", {})

    scores = {name: 0 for name in REGIME_RULES}
    evidence = {name: [] for name in REGIME_RULES}

    def add(name: str, points: int, why: str):
        scores[name] += points
        evidence[name].append(why)

    if adx >= 25 and macd_growing and alignment == "bullish":
        add("Trend Expansion", 3, "ADX strong, MACD growing, HTF bullish")
    if volume_spike and range_ratio > 1.25 and macd_hist > 0:
        add("Trend Expansion", 2, "range and volume expanding")

    if adx < 18 or _alternating_chop(recent_candles):
        add("Mean Reversion", 2, "low ADX or alternating candles")
    if 40 <= rsi <= 60 and indicators.get("price_vs_vwap") == "above":
        add("Mean Reversion", 1, "RSI mid-zone around VWAP")

    if range_ratio < 0.65 and not volume_spike:
        add("Volatility Compression", 3, "recent candle ranges compressed")
    if adx < 20 and abs(macd_hist) < 0.5:
        add("Volatility Compression", 1, "low trend and flat MACD")

    if dte == 0:
        add("Expiry Chaos", 3, "expiry day")
        if now.hour >= 13:
            add("Expiry Chaos", 2, "post 13:00 expiry acceleration")

    if iv_direction == "rising" and iv_rank is not None and float(iv_rank) >= 70:
        add("News Shock", 3, "IV rising from high rank")
    if volume_spike and range_ratio > 2:
        add("News Shock", 2, "abnormally large range plus volume")

    if liquidity.get("events"):
        add("Liquidity Hunt", 2, "liquidity event detected")
    if liquidity.get("hard_block"):
        add("Liquidity Hunt", 3, liquidity.get("block_reason", "liquidity block"))

    if now.hour == 9 and now.minute < 45 and opening_bias in ("breakout_up", "breakdown_down"):
        add("Opening Drive", 3, f"opening range {opening_bias}")
    if now.hour == 9 and volume_spike:
        add("Opening Drive", 1, "opening volume expansion")

    if buildup.get("short_covering") and alignment == "bullish":
        add("Short Covering Rally", 3, "CE short covering with bullish context")
    if buildup.get("buildup_bias") == "bullish" and volume_spike and adx >= 20:
        add("Short Covering Rally", 1, "bullish OI buildup with volume")

    if max(scores.values()) <= 1:
        add("Mixed", 2, "no dominant regime")

    primary = max(scores, key=scores.get)
    top_score = scores[primary]
    confidence = "high" if top_score >= 5 else "medium" if top_score >= 3 else "low"
    rules = REGIME_RULES[primary]
    hard_block = primary in ("Expiry Chaos", "News Shock") and top_score >= 5
    if liquidity.get("hard_block"):
        hard_block = True

    return {
        "regime": primary,
        "confidence": confidence,
        "score": top_score,
        "scores": scores,
        "evidence": evidence[primary],
        "supports": rules["supports"],
        "confluence_adjustment": rules["confluence_adjustment"],
        "risk_multiplier": rules["risk_multiplier"],
        "hard_block": hard_block,
        "block_reason": liquidity.get("block_reason") if liquidity.get("hard_block") else
                        f"{primary} risk too high" if hard_block else "",
    }


def format_regime_summary(regime: dict) -> str:
    if not regime:
        return "regime=unknown"
    evidence = "; ".join(regime.get("evidence", [])[:2])
    return f"{regime.get('regime')} ({regime.get('confidence')}) | {evidence}"
