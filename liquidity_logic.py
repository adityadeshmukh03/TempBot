from __future__ import annotations

from statistics import mean
from typing import Optional


def _body(candle: dict) -> float:
    return abs(float(candle.get("close", 0)) - float(candle.get("open", 0)))


def _range(candle: dict) -> float:
    return max(float(candle.get("high", 0)) - float(candle.get("low", 0)), 0)


def _upper_wick(candle: dict) -> float:
    return max(float(candle.get("high", 0)) - max(float(candle.get("close", 0)), float(candle.get("open", 0))), 0)


def _lower_wick(candle: dict) -> float:
    return max(min(float(candle.get("close", 0)), float(candle.get("open", 0))) - float(candle.get("low", 0)), 0)


def _volume_baseline(candles: list[dict], lookback: int = 10) -> float:
    vols = [float(c.get("volume", 0) or 0) for c in candles[-lookback:]]
    vols = [v for v in vols if v > 0]
    return mean(vols) if vols else 0


def detect_liquidity_events(candles: list[dict], market_context: Optional[dict] = None,
                            oi_result: Optional[dict] = None, direction: str = "CE") -> dict:
    """
    Approximate smart-money/liquidity behaviour from candle and OI data.

    Kite does not provide true orderflow here, so these are conservative
    structural labels. They should tighten or warn, not impersonate footprint
    analytics.
    """
    direction = (direction or "CE").upper()
    if not candles or len(candles) < 6:
        return {
            "available": False,
            "events": [],
            "bias": "unknown",
            "hard_block": False,
            "block_reason": "",
            "notes": ["insufficient candles for liquidity read"],
        }

    last = candles[-1]
    prev_window = candles[-6:-1]
    prev_high = max(float(c["high"]) for c in prev_window)
    prev_low = min(float(c["low"]) for c in prev_window)
    last_high = float(last.get("high", 0))
    last_low = float(last.get("low", 0))
    last_close = float(last.get("close", 0))
    last_open = float(last.get("open", 0))
    last_range = _range(last)
    body = _body(last)
    upper = _upper_wick(last)
    lower = _lower_wick(last)
    vol_base = _volume_baseline(candles[:-1])
    vol = float(last.get("volume", 0) or 0)
    high_volume = vol_base > 0 and vol > vol_base * 1.8

    events = []
    notes = []
    hard_block = False
    block_reason = ""

    swept_high_rejected = last_high > prev_high and last_close < prev_high
    swept_low_reclaimed = last_low < prev_low and last_close > prev_low

    if swept_high_rejected:
        events.append("liquidity_sweep_high_rejection")
        notes.append("last candle swept recent high and closed back below it")
        if direction == "CE":
            hard_block = True
            block_reason = "High sweep rejection - likely bull trap for CE"

    if swept_low_reclaimed:
        events.append("liquidity_sweep_low_reclaim")
        notes.append("last candle swept recent low and reclaimed it")
        if direction == "PE":
            hard_block = True
            block_reason = "Low sweep reclaim - buyers absorbed move, PE fails"

    if last_range > 0:
        upper_pct = upper / last_range
        lower_pct = lower / last_range
        body_pct = body / last_range
        if upper_pct > 0.55 and last_close < last_open:
            events.append("stop_hunt_upper_wick")
            if direction == "CE":
                hard_block = True
                block_reason = block_reason or "Large upper wick rejection"
        if lower_pct > 0.55 and last_close > last_open:
            events.append("absorption_lower_wick")
            if direction == "PE":
                hard_block = True
                block_reason = block_reason or "Large lower wick absorption"
        if high_volume and body_pct < 0.35:
            events.append("absorption_or_failed_auction")
            notes.append("high volume with small body suggests two-way absorption")

    walls = (oi_result or {}).get("oi_walls", {})
    nearest_res = walls.get("nearest_resistance")
    if nearest_res and market_context:
        daily = market_context.get("daily", {})
        spot_position = daily.get("position")
        if spot_position == "above_pdh" and swept_low_reclaimed:
            events.append("breakout_retest_absorption")

    if hard_block:
        bias = "bearish" if direction == "CE" else "bullish"
    elif direction == "PE" and ("liquidity_sweep_high_rejection" in events or "stop_hunt_upper_wick" in events):
        bias = "bearish"
    elif "liquidity_sweep_low_reclaim" in events or "absorption_lower_wick" in events:
        bias = "bullish"
    elif hard_block:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "available": True,
        "events": events,
        "bias": bias,
        "hard_block": hard_block,
        "block_reason": block_reason,
        "notes": notes,
    }


def format_liquidity_summary(liquidity: dict) -> str:
    if not liquidity or not liquidity.get("available"):
        return "Liquidity read unavailable"
    events = ",".join(liquidity.get("events", [])) or "none"
    return f"bias={liquidity.get('bias')} | events={events}"
