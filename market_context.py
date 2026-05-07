from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any, Optional

import pandas as pd


_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_SECONDS = 60


def _empty_context(reason: str) -> dict:
    return {
        "available": False,
        "reason": reason,
        "alignment": "unknown",
        "alignment_score": 0,
        "bias": "neutral",
        "reasons": [],
        "htf": {},
        "daily": {},
        "weekly": {},
        "opening_range": {},
    }


def _normalise_history(data: list[dict]) -> pd.DataFrame:
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def _trend_from_df(df: pd.DataFrame, label: str) -> dict:
    if df.empty or len(df) < 8:
        return {"label": label, "trend": "unknown", "strength": 0, "close": None}

    close = df["close"]
    ema_fast = close.ewm(span=min(9, len(df)), adjust=False).mean()
    ema_slow = close.ewm(span=min(21, len(df)), adjust=False).mean()
    last_close = float(close.iloc[-1])
    fast = float(ema_fast.iloc[-1])
    slow = float(ema_slow.iloc[-1])
    slope = float(ema_fast.iloc[-1] - ema_fast.iloc[-min(4, len(ema_fast))])
    avg_range = float((df["high"] - df["low"]).tail(10).mean() or 0)
    slope_score = abs(slope) / avg_range if avg_range > 0 else 0

    if last_close > fast > slow and slope > 0:
        trend = "bullish"
    elif last_close < fast < slow and slope < 0:
        trend = "bearish"
    else:
        trend = "sideways"

    return {
        "label": label,
        "trend": trend,
        "strength": round(min(slope_score, 3), 2),
        "close": round(last_close, 2),
        "ema_fast": round(fast, 2),
        "ema_slow": round(slow, 2),
        "slope": round(slope, 2),
    }


def _classify_gap(day_open: Optional[float], prev_close: Optional[float]) -> dict:
    if not day_open or not prev_close:
        return {"type": "unknown", "pct": None}
    pct = (day_open - prev_close) / prev_close * 100
    if pct >= 0.35:
        gap_type = "gap_up"
    elif pct <= -0.35:
        gap_type = "gap_down"
    else:
        gap_type = "flat_open"
    return {"type": gap_type, "pct": round(pct, 3)}


def _opening_range(df5: pd.DataFrame, spot_price: Optional[float], now: datetime) -> dict:
    if df5.empty:
        return {"available": False}

    today = now.date()
    today_df = df5[df5["date"].dt.date == today]
    if today_df.empty:
        return {"available": False}

    start = datetime.combine(today, datetime.min.time()).replace(hour=9, minute=15)
    end_15 = start + timedelta(minutes=15)
    end_30 = start + timedelta(minutes=30)
    first_15 = today_df[(today_df["date"] >= start) & (today_df["date"] < end_15)]
    first_30 = today_df[(today_df["date"] >= start) & (today_df["date"] < end_30)]

    ref = first_30 if len(first_30) >= 2 else first_15
    if ref.empty:
        return {"available": False}

    high = float(ref["high"].max())
    low = float(ref["low"].min())
    bias = "inside_range"
    if spot_price:
        if spot_price > high:
            bias = "breakout_up"
        elif spot_price < low:
            bias = "breakdown_down"

    return {
        "available": True,
        "minutes": 30 if len(first_30) >= 2 else 15,
        "high": round(high, 2),
        "low": round(low, 2),
        "range_points": round(high - low, 2),
        "bias": bias,
    }


def _daily_weekly_context(df_day: pd.DataFrame, df5: pd.DataFrame, spot_price: Optional[float], now: datetime) -> tuple[dict, dict]:
    if df_day.empty:
        return {}, {}

    today = now.date()
    prior_days = df_day[df_day["date"].dt.date < today]
    today_intraday = df5[df5["date"].dt.date == today] if not df5.empty else pd.DataFrame()

    prev = prior_days.iloc[-1] if not prior_days.empty else None
    day_open = float(today_intraday["open"].iloc[0]) if not today_intraday.empty else None
    prev_close = float(prev["close"]) if prev is not None else None

    daily = {
        "pdh": round(float(prev["high"]), 2) if prev is not None else None,
        "pdl": round(float(prev["low"]), 2) if prev is not None else None,
        "pdc": round(prev_close, 2) if prev_close else None,
        "day_open": round(day_open, 2) if day_open else None,
        "gap": _classify_gap(day_open, prev_close),
        "position": "unknown",
    }

    if spot_price and daily["pdh"] and daily["pdl"]:
        if spot_price > daily["pdh"]:
            daily["position"] = "above_pdh"
        elif spot_price < daily["pdl"]:
            daily["position"] = "below_pdl"
        else:
            daily["position"] = "inside_previous_range"

    week_start = today - timedelta(days=today.weekday())
    week_df = df_day[df_day["date"].dt.date >= week_start]
    if week_df.empty:
        week_df = df_day.tail(5)
    weekly = {
        "high": round(float(week_df["high"].max()), 2) if not week_df.empty else None,
        "low": round(float(week_df["low"].min()), 2) if not week_df.empty else None,
        "position": "unknown",
    }
    if spot_price and weekly["high"] and weekly["low"]:
        if spot_price >= weekly["high"] * 0.997:
            weekly["position"] = "near_weekly_high"
        elif spot_price <= weekly["low"] * 1.003:
            weekly["position"] = "near_weekly_low"
        else:
            weekly["position"] = "mid_week"

    return daily, weekly


def _build_context(df5: pd.DataFrame, df15: pd.DataFrame, df60: pd.DataFrame, df_day: pd.DataFrame,
                   spot_price: Optional[float], now: datetime) -> dict:
    trend_15 = _trend_from_df(df15, "15m")
    trend_60 = _trend_from_df(df60, "60m")
    daily, weekly = _daily_weekly_context(df_day, df5, spot_price, now)
    opening = _opening_range(df5, spot_price, now)

    score = 0
    reasons = []
    for label, trend in (("15m", trend_15), ("60m", trend_60)):
        if trend["trend"] == "bullish":
            score += 1
            reasons.append(f"{label} bullish")
        elif trend["trend"] == "bearish":
            score -= 1
            reasons.append(f"{label} bearish")

    if daily.get("position") == "above_pdh":
        score += 1
        reasons.append("spot above PDH")
    elif daily.get("position") == "below_pdl":
        score -= 1
        reasons.append("spot below PDL")

    if opening.get("bias") == "breakout_up":
        score += 1
        reasons.append("opening range breakout up")
    elif opening.get("bias") == "breakdown_down":
        score -= 1
        reasons.append("opening range breakdown down")

    if score >= 2:
        alignment = "bullish"
    elif score <= -2:
        alignment = "bearish"
    else:
        alignment = "mixed"

    return {
        "available": True,
        "reason": "",
        "alignment": alignment,
        "alignment_score": score,
        "bias": "long_ce" if alignment == "bullish" else "avoid_ce" if alignment == "bearish" else "selective",
        "reasons": reasons,
        "htf": {"15m": trend_15, "60m": trend_60},
        "daily": daily,
        "weekly": weekly,
        "opening_range": opening,
    }


def get_market_context(kite, underlying_token: int, spot_price: Optional[float] = None,
                       now: Optional[datetime] = None) -> dict:
    """
    Build higher-timeframe context from underlying candles.

    The result is intentionally plain dict data so it can be logged, sent to
    Gemini, and tested without a Kite dependency.
    """
    now = now or datetime.now()
    if not underlying_token:
        return _empty_context("missing underlying token")

    cache_key = f"{underlying_token}:{now.strftime('%Y-%m-%d-%H-%M')}"
    cached = _CACHE.get(cache_key)
    if cached and (now - cached["created_at"]).total_seconds() < _CACHE_SECONDS:
        return cached["value"]

    try:
        to_date = now
        from_intraday = now - timedelta(days=8)
        from_daily = now - timedelta(days=45)
        df5 = _normalise_history(kite.historical_data(underlying_token, from_intraday, to_date, "5minute"))
        df15 = _normalise_history(kite.historical_data(underlying_token, from_intraday, to_date, "15minute"))
        df60 = _normalise_history(kite.historical_data(underlying_token, from_intraday, to_date, "60minute"))
        df_day = _normalise_history(kite.historical_data(underlying_token, from_daily, to_date, "day"))
    except Exception as exc:
        return _empty_context(f"Kite historical context fetch failed: {exc}")

    if df5.empty and df15.empty and df60.empty:
        return _empty_context("no historical context candles returned")

    context = _build_context(df5, df15, df60, df_day, spot_price, now)
    if not spot_price and df5 is not None and not df5.empty:
        context["spot_proxy"] = round(float(df5["close"].iloc[-1]), 2)

    _CACHE[cache_key] = {"created_at": now, "value": context}
    return context


def format_context_summary(context: dict) -> str:
    if not context or not context.get("available"):
        return f"Context unavailable ({(context or {}).get('reason', 'unknown')})"
    htf = context.get("htf", {})
    daily = context.get("daily", {})
    opening = context.get("opening_range", {})
    return (
        f"{context.get('alignment')} score={context.get('alignment_score')} | "
        f"15m={htf.get('15m', {}).get('trend')} | "
        f"1h={htf.get('60m', {}).get('trend')} | "
        f"PD pos={daily.get('position')} | "
        f"gap={daily.get('gap', {}).get('type')} | "
        f"OR={opening.get('bias', 'unknown')}"
    )
