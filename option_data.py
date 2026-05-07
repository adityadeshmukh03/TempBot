# option_data.py
# Fetches Greeks, IV, OI, and computes IV Rank / IV Percentile
# for the chosen option strike from Kite Connect.

import math
from datetime import datetime, date, timedelta
import pandas as pd
import config


_iv_history_cache = {}


# ─── HELPERS ───────────────────────────────────────────

def _days_to_expiry(expiry_date):
    """Calendar days from today to expiry (min 0)."""
    if expiry_date is None:
        return None
    today = date.today()
    if isinstance(expiry_date, datetime):
        expiry_date = expiry_date.date()
    return max((expiry_date - today).days, 0)


def _historical_iv_approximation(kite, underlying_token, days=30):
    """
    Approximate 30-day IV history using realised volatility of the underlying.
    Returns a list of daily HV values (one per trading day).

    Falls back to empty list on any error — callers handle the None case.
    """
    cache_key = (int(underlying_token), date.today().isoformat(), int(days))
    if cache_key in _iv_history_cache:
        return list(_iv_history_cache[cache_key])

    try:
        to  = datetime.now()
        frm = to - timedelta(days=days + 10)          # buffer for weekends
        data = kite.historical_data(
            instrument_token=underlying_token,
            from_date=frm,
            to_date=to,
            interval="day"
        )
        if not data or len(data) < 5:
            _iv_history_cache[cache_key] = []
            return []

        df = pd.DataFrame(data)
        df['ret'] = df['close'].pct_change()
        df = df.dropna()

        # Annualised 5-day rolling HV as daily IV proxy
        df['hv'] = df['ret'].rolling(5).std() * math.sqrt(252) * 100
        df = df.dropna()

        iv_series = df['hv'].tolist()[-days:]       # last `days` values
        _iv_history_cache[cache_key] = iv_series
        return list(iv_series)

    except Exception as e:
        print(f"[option_data] Historical IV approximation failed: {e}")
        _iv_history_cache[cache_key] = []
        return []


def _iv_rank(current_iv, iv_series):
    """IV Rank = (current - min) / (max - min) * 100  →  0-100."""
    if not iv_series or len(iv_series) < 2:
        return None
    lo, hi = min(iv_series), max(iv_series)
    if hi == lo:
        return 50.0
    return round((current_iv - lo) / (hi - lo) * 100, 2)


def _iv_percentile(current_iv, iv_series):
    """% of days in series where IV was below current IV."""
    if not iv_series:
        return None
    below = sum(1 for v in iv_series if v < current_iv)
    return round(below / len(iv_series) * 100, 2)


# ─── INSTRUMENT LOOKUP ────────────────────────────────

def _get_underlying_token(kite, name):
    """
    Return underlying instrument token for the index/stock underlying.
    E.g. 'NIFTY 50' for NIFTY, 'NIFTY BANK' for BANKNIFTY.
    """
    try:
        key = name.upper()
        exchange = config.UNDERLYING_EXCHANGE.get(key, "NSE")
        instruments = kite.instruments(exchange)
        df = pd.DataFrame(instruments)
        search_name = config.UNDERLYING_SYMBOL.get(key, name)
        row = df[df['tradingsymbol'] == search_name]
        if row.empty:
            # fallback: partial match
            row = df[df['name'].str.upper() == name.upper()]
        if row.empty:
            return None
        return int(row.iloc[0]['instrument_token'])
    except Exception as e:
        print(f"[option_data] Underlying token lookup failed: {e}")
        return None


def _get_expiry_date(kite, name, strike, option_type):
    """Return the nearest expiry date for the given option."""
    try:
        exchange = config.INSTRUMENT_EXCHANGE.get(name.upper(), "NFO")
        instruments = kite.instruments(exchange)
        df = pd.DataFrame(instruments)
        result = df[
            (df['name'] == name) &
            (df['strike'] == float(strike)) &
            (df['instrument_type'] == option_type)
        ].sort_values('expiry')
        if result.empty:
            return None
        expiry = result.iloc[0]['expiry']
        return expiry if isinstance(expiry, date) else pd.to_datetime(expiry).date()
    except Exception as e:
        print(f"[option_data] Expiry lookup failed: {e}")
        return None


# ─── MAIN FETCH ────────────────────────────────────────

def fetch_option_data(kite, instrument_token, symbol,
                      underlying_name=None, strike=None, option_type=None):
    """
    Fetch live option Greeks + IV metrics for the given instrument.

    Parameters
    ----------
    kite              : authenticated KiteConnect instance
    instrument_token  : int   — option instrument token
    symbol            : str   — tradingsymbol, e.g. 'NIFTY24APR24400CE'
    underlying_name   : str   — 'NIFTY' / 'BANKNIFTY' etc. (for IV history)
    strike            : float — strike price (for expiry lookup)
    option_type       : str   — 'CE' or 'PE'

    Returns
    -------
    dict with all option fields, None values where data unavailable.
    Never raises — errors are caught and logged.
    """

    # Default safe output — returned as-is on total failure
    result = {
        "iv":             None,
        "iv_rank":        None,
        "iv_percentile":  None,
        "iv_direction":   None,
        "delta":          None,
        "gamma":          None,
        "theta":          None,
        "vega":           None,
        "days_to_expiry": None,
        "oi":             None,
        "oi_change":      None,
        "volume":         None,
        "ltp":            None,
        "best_bid":       None,
        "best_ask":       None,
        "best_bid_qty":   None,
        "best_ask_qty":   None,
        "spread":         None,
        "spread_pct":     None,
    }

    # ── 1. Live quote ──────────────────────────────────
    try:
        exchange = config.INSTRUMENT_EXCHANGE.get((underlying_name or "").upper(), "NFO")
        quote_key = f"{exchange}:{symbol}"
        quote = kite.quote([quote_key])
        data  = quote.get(quote_key, {})

        if not data:
            print(f"[option_data] Empty quote for {symbol}")
            return result

        result['ltp']    = data.get('last_price')
        result['volume'] = data.get('volume')
        result['oi']     = data.get('oi')

        depth = data.get('depth') or {}
        buy_depth = depth.get('buy') or []
        sell_depth = depth.get('sell') or []
        if buy_depth:
            result['best_bid'] = buy_depth[0].get('price')
            result['best_bid_qty'] = buy_depth[0].get('quantity')
        if sell_depth:
            result['best_ask'] = sell_depth[0].get('price')
            result['best_ask_qty'] = sell_depth[0].get('quantity')
        if result['best_bid'] and result['best_ask'] and result['ltp']:
            result['spread'] = round(result['best_ask'] - result['best_bid'], 2)
            result['spread_pct'] = round(result['spread'] / result['ltp'] * 100, 3)

        # OI change: today's current OI minus yesterday's closing OI.
        # oi_day_high is the day's running peak — it approximates yesterday's
        # close only at market open; later in the day it becomes unreliable.
        # We use it as the best available proxy but note the limitation.
        prev_oi_est = data.get('oi_day_high')      # closest proxy to prev-day OI
        if prev_oi_est and prev_oi_est > 0 and result['oi'] is not None:
            result['oi_change'] = round(result['oi'] - prev_oi_est, 0)
        else:
            result['oi_change'] = None              # omit rather than mislead

        # Greeks + IV live from depth/ohlc structure
        greeks = data.get('greeks') or {}
        result['iv']    = greeks.get('iv')    or data.get('implied_volatility')
        result['delta'] = greeks.get('delta')
        result['gamma'] = greeks.get('gamma')
        result['theta'] = greeks.get('theta')
        result['vega']  = greeks.get('vega')

        # Round everything that is not None
        for key in ('iv', 'delta', 'gamma', 'theta', 'vega'):
            if result[key] is not None:
                result[key] = round(float(result[key]), 4)

    except Exception as e:
        print(f"[option_data] Quote fetch failed: {e}")
        return result

    # ── 2. Days to expiry ─────────────────────────────
    if underlying_name and strike and option_type:
        expiry = _get_expiry_date(kite, underlying_name, strike, option_type)
        result['days_to_expiry'] = _days_to_expiry(expiry)

    # ── 3. IV history → IV Rank / Percentile ──────────
    current_iv = result['iv']

    if current_iv is not None and underlying_name:
        underlying_token = _get_underlying_token(kite, underlying_name)
        if underlying_token:
            iv_series = _historical_iv_approximation(kite, underlying_token, days=30)

            if iv_series:
                result['iv_rank']       = _iv_rank(current_iv, iv_series)
                result['iv_percentile'] = _iv_percentile(current_iv, iv_series)
            else:
                print("[option_data] IV series empty — skipping IV Rank/Percentile")
        else:
            print("[option_data] Could not resolve underlying token for IV history")

    # ── 4. IV direction (set externally each candle) ───
    # iv_direction is filled by compare_iv_direction() in main loop.
    result['iv_direction'] = None

    return result


def compare_iv_direction(current_iv, previous_iv):
    """
    Returns 'rising', 'falling', or 'stable'.
    Call this in main.py, passing the previous candle's IV.
    Threshold is configurable via config.IV_DIRECTION_THRESHOLD.
    """
    if current_iv is None or previous_iv is None:
        return None
    threshold = config.IV_DIRECTION_THRESHOLD
    diff = current_iv - previous_iv
    if diff > threshold:
        return "rising"
    elif diff < -threshold:
        return "falling"
    return "stable"
