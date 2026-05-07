# indicators.py

import math
import pandas as pd
import ta

def build_dataframe(candles):
    """Convert candle list to DataFrame"""
    df = pd.DataFrame(candles)
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)
    return df

def calculate_indicators(candles):
    """Takes today's candles, returns clean indicator dict."""

    df = build_dataframe(candles)

    if len(df) < 14:
        print("[WARN] Not enough candles for indicators yet")
        return None

    close = df['close']
    high  = df['high']
    low   = df['low']
    vol   = df['volume']

    # --- TREND ---
    df['ema9']  = ta.trend.EMAIndicator(close, window=9).ema_indicator()
    df['ema21'] = ta.trend.EMAIndicator(close, window=21).ema_indicator()
    df['ema50'] = ta.trend.EMAIndicator(close, window=50).ema_indicator()

    adx = ta.trend.ADXIndicator(high, low, close, window=14)
    df['adx'] = adx.adx()

    # --- MOMENTUM ---
    df['rsi'] = ta.momentum.RSIIndicator(close, window=14).rsi()

    macd = ta.trend.MACD(close)
    df['macd']        = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_hist']   = macd.macd_diff()

    stoch = ta.momentum.StochasticOscillator(high, low, close)
    df['stoch_k'] = stoch.stoch()
    df['stoch_d'] = stoch.stoch_signal()

    # --- VOLUME ---
    df['obv'] = ta.volume.OnBalanceVolumeIndicator(close, vol).on_balance_volume()

    # --- VWAP ---
    df['vwap'] = ta.volume.VolumeWeightedAveragePrice(
        high, low, close, vol
    ).volume_weighted_average_price()

    # --- PRICE ACTION ---
    intraday_high = df['high'].max()
    intraday_low  = df['low'].min()

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    ema_cross_bullish = (prev['ema9'] <= prev['ema21'] and last['ema9'] > last['ema21'])
    ema_cross_bearish = (prev['ema9'] >= prev['ema21'] and last['ema9'] < last['ema21'])

    last_candle_bullish = last['close'] > last['open']
    last_candle_size    = abs(last['close'] - last['open'])
    last_candle_wick    = last['high'] - max(last['close'], last['open'])

    avg_volume   = df['volume'].rolling(10).mean().iloc[-1]
    volume_spike = last['volume'] > avg_volume * 1.5

    indicators = {
        # Trend
        "ema9":               round(last['ema9'], 2),
        "ema21":              round(last['ema21'], 2),
        "ema50":              round(last['ema50'], 2) if not pd.isna(last['ema50']) else None,
        "adx":                round(last['adx'], 2),
        "trend_strong":       last['adx'] > 25,
        "ema_cross_bullish":  ema_cross_bullish,
        "ema_cross_bearish":  ema_cross_bearish,
        "price_vs_ema9":      "above" if last['close'] > last['ema9'] else "below",
        "price_vs_ema21":     "above" if last['close'] > last['ema21'] else "below",
        "price_vs_vwap":      "above" if last['close'] > last['vwap'] else "below",

        # Momentum
        "rsi":                round(last['rsi'], 2),
        "macd":               round(last['macd'], 2),
        "macd_signal":        round(last['macd_signal'], 2),
        "macd_hist":          round(last['macd_hist'], 2),
        "macd_hist_growing":  last['macd_hist'] > prev['macd_hist'],
        "stoch_k":            round(last['stoch_k'], 2),
        "stoch_d":            round(last['stoch_d'], 2),

        # Volume
        "volume_spike":       volume_spike,
        "obv_rising":         last['obv'] > prev['obv'],

        # Price levels
        "current_price":      round(last['close'], 2),
        "vwap":               round(last['vwap'], 2),
        "intraday_high":      round(intraday_high, 2),
        "intraday_low":       round(intraday_low, 2),

        # Last candle
        "last_candle_bullish": last_candle_bullish,
        "last_candle_size":    round(last_candle_size, 2),
        "last_candle_wick":    round(last_candle_wick, 2),
    }

    return indicators

def detect_market_condition(indicators):
    adx = indicators['adx']
    if adx > 25:
        return "TRENDING"
    elif adx < 18:
        return "RANGING"
    else:
        return "MIXED"


# â”€â”€â”€ OI CONFLUENCE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# OI is treated as a PRIMARY signal layer, not side info.
# compute_oi_confluence() is called BEFORE the indicator confluence
# and its score is folded in. It can also hard-veto via `oi_block`.

def compute_oi_confluence(oi_result: dict, spot_price: float, direction: str = "CE") -> dict:
    """
    Score OI signals as primary confluence inputs.

    Direction-aware:
      CE wants bullish OI support and room to the nearest CE wall.
      PE wants bearish OI pressure and room to the nearest PE wall.
    """
    direction = (direction or "CE").upper()
    score = 0
    reasons = []
    missed = []
    oi_block = False
    oi_block_reason = ""

    if not oi_result:
        return {
            "score": 0, "total": 3, "reasons": [], "missed": ["OI data unavailable"],
            "oi_block": False, "oi_block_reason": ""
        }

    pcr = oi_result.get('pcr', {})
    walls = oi_result.get('oi_walls', {})
    buildup = oi_result.get('oi_buildup', {})
    mp = oi_result.get('max_pain', {})

    pcr_oi = pcr.get('pcr_oi')
    buildup_bias = buildup.get('buildup_bias', 'neutral')
    nearest_res = walls.get('nearest_resistance')
    nearest_sup = walls.get('nearest_support')
    max_pain_strike = mp.get('max_pain_strike')

    if pcr_oi is not None:
        if direction == "CE" and pcr_oi < 0.7:
            score += 1
            reasons.append(f"PCR {pcr_oi:.2f} - bullish put writing support")
        elif direction == "PE" and pcr_oi < 0.7:
            score += 1
            reasons.append(f"PCR {pcr_oi:.2f} - bearish sentiment supporting PE")
        elif 0.7 <= pcr_oi <= 1.2:
            score += 0.5
            reasons.append(f"PCR {pcr_oi:.2f} - neutral OI")
        else:
            missed.append(f"PCR {pcr_oi:.2f} against {direction}")

        if direction == "CE" and pcr_oi > 1.3 and buildup_bias == "bearish":
            oi_block = True
            oi_block_reason = f"PCR {pcr_oi:.2f} > 1.3 AND buildup_bias=bearish - smart money positioned against CE"
        elif direction == "PE" and pcr_oi < 0.7 and buildup_bias == "bullish":
            oi_block = True
            oi_block_reason = f"PCR {pcr_oi:.2f} < 0.7 AND buildup_bias=bullish - smart money positioned against PE"
    else:
        missed.append("PCR unavailable")

    if (direction == "CE" and buildup_bias == "bullish") or (direction == "PE" and buildup_bias == "bearish"):
        score += 1
        bull_sig = buildup.get('bullish_signals', 0)
        bear_sig = buildup.get('bearish_signals', 0)
        reasons.append(f"OI buildup bias {buildup_bias.upper()} ({bull_sig} bull vs {bear_sig} bear signals)")
    elif buildup_bias in ("bullish", "bearish"):
        bull_sig = buildup.get('bullish_signals', 0)
        bear_sig = buildup.get('bearish_signals', 0)
        missed.append(f"OI buildup bias {buildup_bias.upper()} against {direction} ({bull_sig} bull vs {bear_sig} bear signals)")
    else:
        missed.append("OI buildup bias NEUTRAL - no strong directional edge")

    if direction == "CE":
        if nearest_res is not None and spot_price is not None:
            dist_to_wall = nearest_res - spot_price
            if dist_to_wall < 0:
                missed.append(f"Price already above nearest CE wall at {nearest_res} - abnormal")
            elif dist_to_wall <= 30:
                oi_block = True
                oi_block_reason = f"Price {spot_price} within {dist_to_wall:.0f} pts of CE OI wall at {nearest_res} - strong resistance ceiling"
            elif dist_to_wall <= 80:
                missed.append(f"Nearest CE wall at {nearest_res} - only {dist_to_wall:.0f} pts away")
            else:
                score += 1
                reasons.append(f"Nearest CE wall at {nearest_res} - {dist_to_wall:.0f} pts clear runway above")
        else:
            missed.append("CE OI wall data unavailable")
    else:
        if nearest_sup is not None and spot_price is not None:
            dist_to_wall = spot_price - nearest_sup
            if dist_to_wall < 0:
                missed.append(f"Price already below nearest PE wall at {nearest_sup} - abnormal")
            elif dist_to_wall <= 30:
                oi_block = True
                oi_block_reason = f"Price {spot_price} within {dist_to_wall:.0f} pts of PE OI wall at {nearest_sup} - strong support floor"
            elif dist_to_wall <= 80:
                missed.append(f"Nearest PE wall at {nearest_sup} - only {dist_to_wall:.0f} pts away")
            else:
                score += 1
                reasons.append(f"Nearest PE wall at {nearest_sup} - {dist_to_wall:.0f} pts clear runway below")
        else:
            missed.append("PE OI wall data unavailable")

    if max_pain_strike and spot_price:
        mp_dist = spot_price - max_pain_strike
        if direction == "CE" and mp_dist > spot_price * 0.015:
            missed.append(f"Spot {mp_dist:.0f} pts above max pain {max_pain_strike} - expiry gravity pulling down")
        elif direction == "CE" and mp_dist < 0:
            reasons.append(f"Spot below max pain {max_pain_strike} - price tends to drift up toward it")
        elif direction == "PE" and mp_dist < 0:
            reasons.append(f"Spot below max pain {max_pain_strike} - bearish max-pain context")
        elif direction == "PE" and mp_dist > spot_price * 0.015:
            missed.append(f"Spot {mp_dist:.0f} pts above max pain {max_pain_strike} - PE needs downside follow-through")

    score = min(math.floor(score), 3)
    return {
        "score": score,
        "total": 3,
        "reasons": reasons,
        "missed": missed,
        "oi_block": oi_block,
        "oi_block_reason": oi_block_reason,
    }


# --- INDICATOR CONFLUENCE ----------------------------------------------------

def compute_confluence(indicators, condition, oi_result=None, spot_price=None, direction: str = "CE"):
    """
    Computes indicator confluence + OI confluence in one pass.
    OI is folded into the total score and can hard-veto via oi_block.
    """
    direction = (direction or "CE").upper()
    score = 0
    reasons = []
    missed = []

    def side(expected_ce, expected_pe):
        return expected_ce if direction == "CE" else expected_pe

    def add_if(ok, reason, miss):
        nonlocal score
        if ok:
            score += 1
            reasons.append(reason)
        else:
            missed.append(miss)

    if condition == "TRENDING":
        ema_side = side("above", "below")
        add_if(indicators['price_vs_ema9'] == ema_side, f"Price {ema_side} EMA9", f"Price not {ema_side} EMA9")
        add_if(indicators['price_vs_ema21'] == ema_side, f"Price {ema_side} EMA21", f"Price not {ema_side} EMA21")
        add_if(indicators['price_vs_vwap'] == ema_side, f"Price {ema_side} VWAP", f"Price not {ema_side} VWAP")
        add_if(indicators['macd_hist_growing'] and (indicators['macd_hist'] > 0 if direction == "CE" else indicators['macd_hist'] < 0), "MACD histogram confirming direction", "MACD histogram not confirming")
        add_if(indicators['volume_spike'], "Volume spike present", "No volume spike")
        add_if(indicators['ema_cross_bullish'] if direction == "CE" else indicators['ema_cross_bearish'], "EMA crossover confirming direction", "No EMA crossover")
        add_if(indicators['obv_rising'] if direction == "CE" else not indicators['obv_rising'], "OBV confirms direction", "OBV not confirming")
        add_if(indicators['last_candle_bullish'] if direction == "CE" else not indicators['last_candle_bullish'], "Last candle confirms direction", "Last candle not confirming")
        indicator_total = 8
        indicator_required = 4

    elif condition == "RANGING":
        if direction == "CE":
            add_if(40 <= indicators['rsi'] <= 55 or indicators['rsi'] < 40, "RSI in CE bounce zone", f"RSI at {indicators['rsi']} - not in CE range entry zone")
            stoch_ok = indicators['stoch_k'] > indicators['stoch_d'] and indicators['stoch_k'] < 60
        else:
            add_if(45 <= indicators['rsi'] <= 60, "RSI in PE fade zone", f"RSI at {indicators['rsi']} - not in PE range entry zone")
            stoch_ok = indicators['stoch_k'] < indicators['stoch_d'] and indicators['stoch_k'] > 40
        add_if(stoch_ok, "Stochastic confirms direction", "Stochastic not confirming")
        vwap_side = side("above", "below")
        add_if(indicators['price_vs_vwap'] == vwap_side, f"Price {vwap_side} VWAP", f"Price not {vwap_side} VWAP")
        price = indicators['current_price']
        vwap = indicators['vwap']
        near_vwap = abs(price - vwap) / vwap < 0.002 and (price >= vwap if direction == "CE" else price <= vwap)
        add_if(near_vwap, "Price near VWAP from correct side", "Price not near VWAP from correct side")
        candle_ok = indicators['last_candle_bullish'] and indicators['last_candle_wick'] < indicators['last_candle_size'] if direction == "CE" else (not indicators['last_candle_bullish']) and indicators['last_candle_wick'] < indicators['last_candle_size']
        add_if(candle_ok, "Candle structure confirms direction", "Last candle not clean directional")
        add_if(indicators['obv_rising'] if direction == "CE" else not indicators['obv_rising'], "OBV confirms direction", "OBV not confirming")
        indicator_total = 6
        indicator_required = 4

    else:
        vwap_side = side("above", "below")
        add_if(indicators['price_vs_vwap'] == vwap_side, f"Price {vwap_side} VWAP", f"Price not {vwap_side} VWAP")
        add_if(50 <= indicators['rsi'] <= 65 if direction == "CE" else 35 <= indicators['rsi'] <= 50, "RSI in directional mixed-zone", f"RSI at {indicators['rsi']} outside clean range")
        add_if(indicators['macd_hist'] > 0 if direction == "CE" else indicators['macd_hist'] < 0, "MACD histogram confirms direction", "MACD histogram against direction")
        add_if(indicators['last_candle_bullish'] if direction == "CE" else not indicators['last_candle_bullish'], "Last candle confirms direction", "Last candle not confirming")
        add_if(indicators['volume_spike'], "Volume spike", "No volume spike")
        ema_side = side("above", "below")
        add_if(indicators['price_vs_ema9'] == ema_side and indicators['price_vs_ema21'] == ema_side, f"Price {ema_side} both EMA9 and EMA21", f"Price not {ema_side} both EMAs")
        indicator_total = 6
        indicator_required = 5

    oi_confluence = compute_oi_confluence(oi_result, spot_price, direction=direction)
    score += oi_confluence['score']
    reasons += oi_confluence['reasons']
    missed += oi_confluence['missed']

    total = indicator_total + oi_confluence['total']
    required = indicator_required + 1

    return {
        "score": score,
        "total": total,
        "required": required,
        "met": score >= required and not oi_confluence['oi_block'],
        "reasons": reasons,
        "missed": missed,
        "oi_confluence": oi_confluence,
        "oi_block": oi_confluence['oi_block'],
        "oi_block_reason": oi_confluence['oi_block_reason'],
        "confluence_required": required,
    }

# â”€â”€â”€ OPTION DATA ENRICHMENT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def enrich_with_option_data(indicators, option_data):
    """
    Merge option Greeks + IV metrics into the indicators dict.
    Returns a new combined dict â€” does not mutate either input.
    Safe: if option_data is None or partial, just omits missing keys.
    """
    if indicators is None:
        return None

    enriched = dict(indicators)

    if not option_data:
        return enriched

    option_keys = [
        'iv', 'iv_rank', 'iv_percentile', 'iv_direction',
        'delta', 'gamma', 'theta', 'vega',
        'days_to_expiry', 'oi', 'oi_change', 'volume'
    ]
    for key in option_keys:
        enriched[f"opt_{key}"] = option_data.get(key)

    return enriched
