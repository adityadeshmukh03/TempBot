# hard_filters.py
#
# Hard "NO TRADE" filters — checked before any analysis runs.
# If ANY filter trips, we block immediately. No Gemini call, no confluence.
#
# Each filter returns:
#   (blocked: bool, reason: str)
#
# run_all_filters() returns the first blocking filter found,
# or (False, "") if trading is permitted.
# ────────────────────────────────────────────────────────

from datetime import datetime, date
import config


# ─── INDIVIDUAL FILTERS ────────────────────────────────

def filter_iv_rank(option_data: dict) -> tuple[bool, str]:
    """Block if IV Rank is too high — options are overpriced."""
    iv_rank = option_data.get('iv_rank') if option_data else None
    if iv_rank is None:
        return False, ""   # can't block on missing data
    if iv_rank >= config.FILTER_IV_RANK_MAX:
        return True, f"IV Rank {iv_rank:.1f} >= {config.FILTER_IV_RANK_MAX} — options too expensive"
    return False, ""


def filter_expiry_day(option_data: dict) -> tuple[bool, str]:
    """
    Block on expiry day after the configured cutoff time.
    Expiry day + late = theta accelerating, wide spreads, illiquid.
    """
    dte = option_data.get('days_to_expiry') if option_data else None
    if dte is None:
        return False, ""
    if dte == 0:
        now = datetime.now()
        cutoff_h, cutoff_m = map(int, config.FILTER_EXPIRY_CUTOFF.split(":"))
        cutoff = now.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0)
        if now >= cutoff:
            return True, (
                f"Expiry day and time {now.strftime('%H:%M')} >= "
                f"{config.FILTER_EXPIRY_CUTOFF} — theta too aggressive"
            )
    return False, ""


def filter_dead_market(indicators: dict) -> tuple[bool, str]:
    """Block if ADX is too low — market is in chop, no directional energy."""
    adx = indicators.get('adx') if indicators else None
    if adx is None:
        return False, ""
    if adx < config.FILTER_ADX_MIN:
        return True, f"ADX {adx:.1f} < {config.FILTER_ADX_MIN} — dead/choppy market, no trend energy"
    return False, ""


def filter_no_candles(candle_count: int) -> tuple[bool, str]:
    """Block if we don't have enough candles for reliable indicators."""
    if candle_count < 14:
        return True, f"Only {candle_count} candles — need 14 minimum for indicators"
    return False, ""


def filter_pre_market_chop() -> tuple[bool, str]:
    """
    Block the first 15 minutes after market open (9:15–9:30).
    Opening range is erratic — false breakouts are common.
    """
    now   = datetime.now()
    open_ = now.replace(hour=9, minute=15, second=0, microsecond=0)
    guard = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if open_ <= now < guard:
        return True, f"Opening 15-min chop period (9:15–9:30) — no trades until 9:30"
    return False, ""


def filter_market_close_approach() -> tuple[bool, str]:
    """
    Block after 3:15 PM.
    Liquidity drops sharply, spreads widen, forced unwinding distorts price.
    """
    now   = datetime.now()
    close = now.replace(hour=15, minute=15, second=0, microsecond=0)
    if now >= close:
        return True, f"After 3:15 PM — market closing, no new entries"
    return False, ""


# ─── MASTER GATES ──────────────────────────────────────
# Split into two callables so we avoid running the full suite twice in main.py:
#   Pass 1 — cheap filters that need no Kite API calls (time, candle count, ADX)
#   Pass 2 — option-data-dependent filters (IV Rank, expiry day)
# Call run_cheap_filters() first, then run_option_filters() after fetching option data.
# run_all_filters() is kept as a convenience wrapper for callers that have all data.

def _first_blocking(checks) -> tuple[bool, str]:
    for blocked, reason in checks:
        if blocked:
            return True, reason
    return False, ""


def run_cheap_filters(
    indicators:   dict,
    candle_count: int,
    direction:    str = "CE",
) -> tuple[bool, str]:
    """
    Pass 1 — filters that require no Kite API calls.
    Run these before fetching option data to fail fast cheaply.
    """
    return _first_blocking([
        filter_no_candles(candle_count),
        filter_pre_market_chop(),
        filter_market_close_approach(),
        filter_dead_market(indicators),
    ])


def run_option_filters(
    option_data: dict,
) -> tuple[bool, str]:
    """
    Pass 2 — filters that require option data (IV Rank, expiry day).
    Only call after fetch_option_data() succeeds.
    """
    return _first_blocking([
        filter_iv_rank(option_data),
        filter_expiry_day(option_data),
    ])


def run_all_filters(
    indicators:    dict,
    option_data:   dict,
    candle_count:  int,
    direction:     str = "CE",
) -> tuple[bool, str]:
    """
    Convenience wrapper — runs all filters in one call.
    Prefer run_cheap_filters() + run_option_filters() in main.py to avoid
    running the cheap filters a second time after the option data fetch.
    """
    blocked, reason = run_cheap_filters(indicators, candle_count, direction=direction)
    if blocked:
        return True, reason
    return run_option_filters(option_data)
