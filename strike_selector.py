# strike_selector.py
#
# Responsibilities:
#   1. detect_direction()     - CE or PE based on market context + indicators
#   2. get_atm_strike()       - nearest valid strike to spot price
#   3. select_best_strike()   - pick strike closest to TARGET_DELTA
#   4. resolve_instrument()   - return token + symbol + strike + direction
#
# Called once per candle cycle in main.py BEFORE fetch_option_data().
# Returns None if direction is mixed/unclear - bot sits out that candle.

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

import config


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def detect_direction(market_context, indicators, regime, oi_result):
    market_context = market_context or {}
    regime = regime or {}
    oi_result = oi_result or {}

    if _get(regime, "hard_block") is True:
        print(f"[DIRECTION] Regime hard block: {_get(regime, 'block_reason', '')}")
        return None

    alignment = market_context.get("alignment", "mixed")
    alignment_score = int(market_context.get("alignment_score") or 0)
    if alignment == "mixed" and alignment_score == 0:
        print("[DIRECTION] Mixed HTF alignment with zero score - sit out.")
        return None
    mixed_threshold = int(getattr(config, "DIRECTION_MIXED_THRESHOLD", 0) or 0)
    if abs(alignment_score) <= mixed_threshold and alignment != "mixed":
        print(f"[DIRECTION] Alignment score {alignment_score} does not clear threshold {mixed_threshold}.")
        return None

    htf = market_context.get("htf", {})
    trend_15 = (htf.get("15m") or {}).get("trend")
    trend_60 = (htf.get("60m") or {}).get("trend")
    strong_threshold = getattr(config, "DIRECTION_STRONG_THRESHOLD", 2)

    if alignment_score >= strong_threshold and trend_15 == "bullish" and trend_60 == "bullish":
        print("[DIRECTION] Strong bullish HTF alignment - CE.")
        return "CE"
    if alignment_score <= -strong_threshold and trend_15 == "bearish" and trend_60 == "bearish":
        print("[DIRECTION] Strong bearish HTF alignment - PE.")
        return "PE"

    pcr = oi_result.get("pcr", {})
    buildup = oi_result.get("oi_buildup", {})
    sentiment = pcr.get("sentiment")
    pcr_oi = pcr.get("pcr_oi")
    buildup_bias = buildup.get("buildup_bias")

    if alignment == "bullish":
        if sentiment == "bearish":
            print("[DIRECTION] Bullish price context contradicted by bearish PCR - sit out.")
            return None
        print("[DIRECTION] Moderate bullish alignment - CE.")
        return "CE"

    if alignment == "bearish":
        if sentiment == "bullish":
            print("[DIRECTION] Bearish price context contradicted by bullish PCR - sit out.")
            return None
        print("[DIRECTION] Moderate bearish alignment - PE.")
        return "PE"

    if alignment == "mixed":
        if buildup_bias == "bullish" and pcr_oi is not None and pcr_oi < 0.8:
            print("[DIRECTION] Mixed price context with bullish OI consensus - CE.")
            return "CE"
        if buildup_bias == "bearish" and pcr_oi is not None and pcr_oi > 1.3:
            print("[DIRECTION] Mixed price context with bearish OI consensus - PE.")
            return "PE"
        print("[DIRECTION] Mixed context without OI consensus - sit out.")
        return None

    print("[DIRECTION] No directional edge detected - sit out.")
    return None


def _strike_step(instrument_name: str) -> int:
    return int(config.STRIKE_STEP or config.STRIKE_STEP_MAP.get(instrument_name.upper(), 50))


def get_atm_strike(spot_price, instrument_name):
    step = _strike_step(instrument_name)
    return round(round(float(spot_price) / step) * step)


def _normalise_expiry(expiry):
    if isinstance(expiry, datetime):
        return expiry.date()
    if isinstance(expiry, date):
        return expiry
    return pd.to_datetime(expiry).date()


def _extract_delta(data: dict):
    greeks = data.get("greeks") or {}
    delta = greeks.get("delta")
    if delta is None:
        delta = data.get("delta")
    if delta is None:
        return None
    try:
        return abs(float(delta))
    except (TypeError, ValueError):
        return None


def _quote_delta(kite, exchange: str, tradingsymbol: str):
    quote_key = f"{exchange}:{tradingsymbol}"
    quote = kite.quote([quote_key])
    data = quote.get(quote_key, {})
    return _extract_delta(data)


def select_best_strike(kite, instrument_name, direction, spot_price, expiry_date):
    instrument_name = instrument_name.upper()
    direction = direction.upper()
    exchange = config.INSTRUMENT_EXCHANGE.get(instrument_name, "NFO")
    step = _strike_step(instrument_name)
    atm = get_atm_strike(spot_price, instrument_name)
    candidates = (
        [atm, atm + step, atm + (2 * step), atm - step]
        if direction == "CE"
        else [atm, atm - step, atm - (2 * step), atm + step]
    )

    instruments = pd.DataFrame(kite.instruments(exchange))
    if instruments.empty:
        return None

    expiry_date = _normalise_expiry(expiry_date)
    instruments["expiry_norm"] = pd.to_datetime(instruments["expiry"]).dt.date
    chain = instruments[
        (instruments["name"].astype(str).str.upper() == instrument_name) &
        (instruments["instrument_type"] == direction) &
        (instruments["expiry_norm"] == expiry_date)
    ].copy()
    if chain.empty:
        return None

    target = float(config.TARGET_DELTA)
    tolerance = float(config.DELTA_TOLERANCE)
    best = None
    fallback = None

    for strike in candidates:
        row = chain[chain["strike"].astype(float) == float(strike)]
        if row.empty:
            continue
        inst = row.iloc[0]
        tradingsymbol = inst["tradingsymbol"]
        token = int(inst["instrument_token"])
        delta = _quote_delta(kite, exchange, tradingsymbol)
        candidate = {
            "instrument_token": token,
            "tradingsymbol": tradingsymbol,
            "strike": float(strike),
            "delta": delta,
        }
        if strike == atm:
            fallback = candidate
        if delta is None:
            continue
        distance = abs(delta - target)
        if distance <= tolerance and (best is None or distance < best["distance"]):
            best = {**candidate, "distance": distance}

    selected = best or fallback
    if selected is None:
        return None
    return (
        selected["instrument_token"],
        selected["tradingsymbol"],
        selected["strike"],
        selected["delta"],
    )


def resolve_instrument(kite, instrument_name, direction, spot_price):
    try:
        instrument_name = instrument_name.upper()
        direction = direction.upper()
        exchange = config.INSTRUMENT_EXCHANGE.get(instrument_name)
        if not exchange:
            print(f"[DIRECTION] No exchange mapping for {instrument_name}")
            return None

        instruments = pd.DataFrame(kite.instruments(exchange))
        if instruments.empty:
            print(f"[DIRECTION] Empty instruments list for {exchange}")
            return None

        chain = instruments[
            (instruments["name"].astype(str).str.upper() == instrument_name) &
            (instruments["instrument_type"] == direction)
        ].copy()
        if chain.empty:
            print(f"[DIRECTION] No {direction} chain found for {instrument_name} on {exchange}")
            return None

        chain["expiry_norm"] = pd.to_datetime(chain["expiry"]).dt.date
        nearest_expiry = sorted(chain["expiry_norm"].dropna().unique())[0]
        selected = select_best_strike(
            kite, instrument_name, direction, spot_price, nearest_expiry
        )
        if selected is None:
            print(f"[DIRECTION] Could not select strike for {instrument_name} {direction}")
            return None

        token, tradingsymbol, strike, delta = selected
        return {
            "instrument_token": int(token),
            "tradingsymbol": tradingsymbol,
            "strike": float(strike),
            "direction": direction,
            "expiry": nearest_expiry,
            "delta": delta,
            "exchange": exchange,
        }
    except Exception as exc:
        print(f"[DIRECTION] Instrument resolution failed: {exc}")
        return None
