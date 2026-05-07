# oi_analysis.py
# Full option chain OI analysis:
#   - PCR (Put-Call Ratio) — per strike + overall
#   - Max Pain — where option writers win
#   - OI Walls — heavy CE/PE writing (real support/resistance)
#   - OI Buildup vs Unwinding — fresh money vs short covering
#   - Strike-wise OI heatmap data — for terminal display

import math
import json
from datetime import date, datetime
from pathlib import Path
import pandas as pd
import config


# ─── CONSTANTS ─────────────────────────────────────────

# Strikes to show either side of ATM in heatmap / analysis
HEATMAP_STRIKES_EACH_SIDE = 10

# OI wall threshold — top N strikes by OI count as walls
OI_WALL_TOP_N = 3

# Buildup thresholds
OI_BUILDUP_THRESHOLD   = 0.10   # +10% OI increase  = fresh buildup
OI_UNWINDING_THRESHOLD = -0.10  # -10% OI decrease  = unwinding


# ─── OI BASELINE CACHE ─────────────────────────────────
# Problem: Kite's oi_day_high is the running intraday peak, not yesterday's
# close. By 11 AM it equals today's highest OI seen so far — making
# oi_change_pct near-zero even when OI has actually moved significantly.
#
# Fix: On the first fetch of the day we snapshot each strike's current OI
# as the baseline. All subsequent oi_change_pct calculations use this
# snapshot instead of oi_day_high, giving a stable "change since open" figure.
#
# The cache is module-level so it persists across run_oi_analysis() calls
# but resets automatically if the date changes (new trading day).

_oi_baseline_cache: dict = {}   # {tradingsymbol: oi_at_open}
_oi_baseline_date:  str  = ""   # "YYYY-MM-DD" — cache validity guard


_chain_cache = {}


def _baseline_path() -> Path:
    return Path(getattr(config, "OI_BASELINE_PATH", "state/oi_baseline.json"))


def _load_oi_baseline(today: str):
    global _oi_baseline_cache, _oi_baseline_date
    path = _baseline_path()
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        print(f"[OI CACHE] Could not read baseline cache: {exc}")
        return
    if payload.get("date") != today:
        return
    _oi_baseline_cache = {
        key: float(value)
        for key, value in (payload.get("baseline", {}) or {}).items()
    }
    _oi_baseline_date = today
    print(f"[OI CACHE] Restored {len(_oi_baseline_cache)} OI baselines for {today}")


def _save_oi_baseline():
    path = _baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": _oi_baseline_date, "baseline": _oi_baseline_cache}
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=float))
    except Exception as exc:
        print(f"[OI CACHE] Could not persist baseline cache: {exc}")


def _get_oi_baseline(sym: str, current_oi: float) -> float:
    """
    Return the baseline OI for a symbol (OI at the time of first fetch today).
    On first call for a symbol, or after a date rollover, seeds the cache.
    """
    global _oi_baseline_cache, _oi_baseline_date

    today = datetime.now().strftime("%Y-%m-%d")

    if not _oi_baseline_date:
        _load_oi_baseline(today)

    # Reset cache on new trading day
    if today != _oi_baseline_date:
        _oi_baseline_cache = {}
        _oi_baseline_date  = today
        print(f"[OI CACHE] New trading day — baseline cache reset for {today}")

    if sym not in _oi_baseline_cache:
        _oi_baseline_cache[sym] = float(current_oi or 0)
        _save_oi_baseline()
        # Baselines are persisted so a mid-session restart can reuse them.
        # If the first run starts late, that snapshot is still the best available baseline.
    return _oi_baseline_cache[sym]


# ─── CHAIN FETCH ───────────────────────────────────────

def fetch_option_chain(kite, underlying_name: str, expiry_date=None) -> pd.DataFrame:
    """
    Pull full NFO option chain for the given underlying.
    Returns a DataFrame with one row per strike/type combo.

    Columns guaranteed:
        strike, instrument_type (CE/PE), instrument_token,
        tradingsymbol, expiry
    Quote columns added by enrich_with_quotes():
        ltp, oi, oi_prev (yesterday OI approx), volume, iv
    """
    try:
        today_key = datetime.now().strftime("%Y-%m-%d")
        cache_key = (underlying_name, str(expiry_date or "nearest"), today_key)
        if cache_key in _chain_cache:
            return _chain_cache[cache_key].copy()

        exchange = config.INSTRUMENT_EXCHANGE.get(underlying_name.upper(), "NFO")
        instruments = kite.instruments(exchange)
        df = pd.DataFrame(instruments)

        # Filter to this underlying
        chain = df[df['name'] == underlying_name].copy()
        if chain.empty:
            print(f"[oi_analysis] No instruments found for {underlying_name}")
            return pd.DataFrame()

        # Nearest expiry if not specified
        chain['expiry'] = pd.to_datetime(chain['expiry']).dt.date
        if expiry_date is None:
            expiry_date = chain['expiry'].min()

        chain = chain[chain['expiry'] == expiry_date].copy()
        chain = chain[chain['instrument_type'].isin(['CE', 'PE'])].copy()
        chain = chain.sort_values('strike').reset_index(drop=True)

        print(f"[oi_analysis] Chain loaded: {underlying_name} | "
              f"Expiry: {expiry_date} | Rows: {len(chain)}")
        _chain_cache[cache_key] = chain.copy()
        return chain

    except Exception as e:
        print(f"[oi_analysis] fetch_option_chain error: {e}")
        return pd.DataFrame()


def enrich_with_quotes(kite, chain: pd.DataFrame) -> pd.DataFrame:
    """
    Batch-fetch live quotes for all strikes in the chain.
    Adds columns: ltp, oi, volume, iv, oi_change_pct
    Kite quote API accepts up to 500 symbols per call.
    """
    if chain.empty:
        return chain

    exchange = chain['exchange'].iloc[0] if 'exchange' in chain.columns and not chain.empty else "NFO"
    symbols = [f"{exchange}:{s}" for s in chain['tradingsymbol']]

    # Chunk into 500-symbol batches (Kite limit)
    all_quotes = {}
    for i in range(0, len(symbols), 500):
        batch = symbols[i: i + 500]
        try:
            q = kite.quote(batch)
            all_quotes.update(q)
        except Exception as e:
            print(f"[oi_analysis] Quote batch {i//500} failed: {e}")

    # Map back onto the chain
    ltps, ois, vols, ivs = [], [], [], []
    for sym in chain['tradingsymbol']:
        key  = f"{exchange}:{sym}"
        data = all_quotes.get(key, {})

        ltps.append(data.get('last_price'))
        ois.append(data.get('oi', 0) or 0)
        vols.append(data.get('volume', 0) or 0)

        greeks = data.get('greeks') or {}
        iv_val = greeks.get('iv') or data.get('implied_volatility')
        ivs.append(round(float(iv_val), 2) if iv_val else None)

    chain = chain.copy()
    chain['ltp']    = ltps
    chain['oi']     = ois
    chain['volume'] = vols
    chain['iv']     = ivs

    # OI change % — use stable open-of-day baseline instead of oi_day_high.
    # oi_day_high is the running intraday peak so by 11 AM it no longer
    # reflects yesterday's close — it equals today's highest OI seen so far,
    # making oi_change_pct artificially small for the rest of the session.
    # _get_oi_baseline() snapshots each strike's OI on first fetch of the day
    # and reuses that value as the denominator for all subsequent calls.
    oi_baselines = []
    for sym, current_oi in zip(chain['tradingsymbol'], ois):
        oi_baselines.append(_get_oi_baseline(sym, current_oi))

    chain['oi_prev'] = oi_baselines

    def _pct(row):
        if row['oi_prev'] and row['oi_prev'] > 0:
            return round((row['oi'] - row['oi_prev']) / row['oi_prev'], 4)
        return 0.0

    chain['oi_change_pct'] = chain.apply(_pct, axis=1)
    return chain


# ─── PCR ───────────────────────────────────────────────

def compute_pcr(chain: pd.DataFrame) -> dict:
    """
    Put-Call Ratio on OI and volume.
    Returns overall PCR + per-strike PCR dict.

    Interpretation:
        PCR < 0.7   → bullish (more CE buying / PE writing)
        0.7–1.0     → neutral
        1.0–1.2     → mildly bearish
        > 1.2       → strongly bearish / fear
    """
    if chain.empty:
        return {}

    ce = chain[chain['instrument_type'] == 'CE']
    pe = chain[chain['instrument_type'] == 'PE']

    total_ce_oi  = ce['oi'].sum()
    total_pe_oi  = pe['oi'].sum()
    total_ce_vol = ce['volume'].sum()
    total_pe_vol = pe['volume'].sum()

    pcr_oi  = round(total_pe_oi  / total_ce_oi,  3) if total_ce_oi  else None
    pcr_vol = round(total_pe_vol / total_ce_vol, 3) if total_ce_vol else None

    # Per-strike PCR (OI-based)
    strikes = sorted(chain['strike'].unique())
    per_strike = {}
    for strike in strikes:
        ce_oi = chain[(chain['strike'] == strike) & (chain['instrument_type'] == 'CE')]['oi'].sum()
        pe_oi = chain[(chain['strike'] == strike) & (chain['instrument_type'] == 'PE')]['oi'].sum()
        if ce_oi > 0:
            per_strike[strike] = round(pe_oi / ce_oi, 3)

    # Sentiment label
    if pcr_oi is None:
        sentiment = "unknown"
    elif pcr_oi < 0.7:
        sentiment = "bullish"
    elif pcr_oi < 1.0:
        sentiment = "neutral"
    elif pcr_oi < 1.2:
        sentiment = "mildly_bearish"
    else:
        sentiment = "strongly_bearish"

    result = {
        "pcr_oi":       pcr_oi,
        "pcr_volume":   pcr_vol,
        "sentiment":    sentiment,
        "total_ce_oi":  int(total_ce_oi),
        "total_pe_oi":  int(total_pe_oi),
        "per_strike":   per_strike,       # {strike: pcr_oi}
    }
    print(f"[PCR] OI: {pcr_oi} | Vol: {pcr_vol} | Sentiment: {sentiment}")
    return result


# ─── MAX PAIN ──────────────────────────────────────────

def compute_max_pain(chain: pd.DataFrame) -> dict:
    """
    Max Pain = strike where total payout to option buyers is minimised.
    i.e. the point where option writers (FIIs / big players) lose least.

    Algorithm: for each candidate strike, sum up ITM intrinsic value
    across all CE and PE strikes, weighted by OI.
    """
    if chain.empty:
        return {}

    strikes = sorted(chain['strike'].unique())

    ce_rows = chain[chain['instrument_type'] == 'CE'].set_index('strike')['oi']
    pe_rows = chain[chain['instrument_type'] == 'PE'].set_index('strike')['oi']

    pain = {}
    for candidate in strikes:
        # CE pain: sum of max(candidate - strike, 0) * OI for all CE strikes
        ce_pain = sum(
            max(candidate - s, 0) * ce_rows.get(s, 0)
            for s in strikes
        )
        # PE pain: sum of max(strike - candidate, 0) * OI for all PE strikes
        pe_pain = sum(
            max(s - candidate, 0) * pe_rows.get(s, 0)
            for s in strikes
        )
        pain[candidate] = ce_pain + pe_pain

    max_pain_strike = min(pain, key=pain.get)

    result = {
        "max_pain_strike": max_pain_strike,
        "pain_by_strike":  pain,           # full dict for charting
    }
    print(f"[MAX PAIN] Strike: {max_pain_strike}")
    return result


# ─── OI WALLS ──────────────────────────────────────────

def find_oi_walls(chain: pd.DataFrame, spot_price: float, top_n: int = OI_WALL_TOP_N) -> dict:
    """
    OI walls = strikes with heaviest writing = real support/resistance.

    CE writing (high CE OI above spot) → resistance wall
    PE writing (high PE OI below spot) → support wall

    Strategy for nearest_resistance / nearest_support:
      1. Take the top 10 strikes by OI on each side as the candidate pool.
         (top_n=3 is used only for the *displayed* wall list, not for
         nearest detection — a small nearby wall would be missed if we only
         looked at the top-3 by OI.)
      2. From that pool, nearest_resistance = the strike closest to (just
         above) spot price, i.e. the immediate ceiling regardless of OI rank.
      3. resistance_walls / support_walls still show only top_n for display.

    Example: spot=24400, CE OI ranks: 24500 (rank 4), 24600 (rank 1),
             24700 (rank 2), 24800 (rank 3).
    Old logic (top-3 only): nearest_resistance = 24600 — misses 24500.
    New logic (top-10 pool): nearest_resistance = 24500 — correct.
    """
    if chain.empty or not spot_price:
        return {}

    PROXIMITY_POOL = 10   # wider candidate pool for nearest detection

    # Top-N for display
    ce_above_display = chain[
        (chain['instrument_type'] == 'CE') &
        (chain['strike'] > spot_price)
    ].nlargest(top_n, 'oi')[['strike', 'oi', 'ltp', 'iv']].reset_index(drop=True)

    pe_below_display = chain[
        (chain['instrument_type'] == 'PE') &
        (chain['strike'] < spot_price)
    ].nlargest(top_n, 'oi')[['strike', 'oi', 'ltp', 'iv']].reset_index(drop=True)

    resistance_walls = ce_above_display.to_dict('records')
    support_walls    = pe_below_display.to_dict('records')

    # Wider pool for nearest detection — finds the true closest significant wall
    ce_pool = chain[
        (chain['instrument_type'] == 'CE') &
        (chain['strike'] > spot_price)
    ].nlargest(PROXIMITY_POOL, 'oi')

    pe_pool = chain[
        (chain['instrument_type'] == 'PE') &
        (chain['strike'] < spot_price)
    ].nlargest(PROXIMITY_POOL, 'oi')

    # Nearest = lowest CE strike in pool (closest ceiling above spot)
    nearest_resistance = (
        ce_pool['strike'].min() if not ce_pool.empty else None
    )
    # Nearest = highest PE strike in pool (closest floor below spot)
    nearest_support = (
        pe_pool['strike'].max() if not pe_pool.empty else None
    )

    result = {
        "resistance_walls":   resistance_walls,   # list of {strike, oi, ltp, iv}
        "support_walls":      support_walls,
        "nearest_resistance": nearest_resistance,
        "nearest_support":    nearest_support,
    }

    print(f"[OI WALLS] Resistance: {[w['strike'] for w in resistance_walls]} | "
          f"Support: {[w['strike'] for w in support_walls]} | "
          f"Nearest Res: {nearest_resistance} | Nearest Sup: {nearest_support}")
    return result


# ─── OI BUILDUP vs UNWINDING ───────────────────────────

def classify_oi_buildup(chain: pd.DataFrame, spot_price: float) -> dict:
    """
    Classify each strike as:
        LONG_BUILDUP   — price up + OI up      → bulls adding
        SHORT_BUILDUP  — price down + OI up    → bears adding (bearish)
        LONG_UNWINDING — price down + OI down  → bulls exiting (bearish)
        SHORT_COVERING — price up + OI down    → bears covering (bullish)
        NEUTRAL        — no significant change

    Since we don't have tick-by-tick price history per strike, we use:
        - ltp vs oi_prev to infer LTP direction is approximated by
          comparing ltp to theoretical value (mid-point heuristic).
        - oi_change_pct for OI direction.

    Note: A cleaner implementation stores last candle's LTP per strike.
    This version uses ltp > 0 and oi_change_pct as the primary signal.
    """
    if chain.empty:
        return {}

    summary = {
        "long_buildup":   [],   # CE strikes where fresh longs added
        "short_buildup":  [],   # PE strikes where fresh shorts added (bearish)
        "long_unwinding": [],   # strikes where longs exiting
        "short_covering": [],   # strikes where shorts covering
    }

    for _, row in chain.iterrows():
        oi_chg = row.get('oi_change_pct', 0) or 0
        strike = row['strike']
        itype  = row['instrument_type']

        is_oi_up   = oi_chg > OI_BUILDUP_THRESHOLD
        is_oi_down = oi_chg < OI_UNWINDING_THRESHOLD

        # We use a simple heuristic:
        # CE above spot with rising OI  → resistance building (shorts adding / writers)
        # PE below spot with rising OI  → support building
        # CE above spot with falling OI → shorts covering → bullish squeeze possible
        # PE below spot with falling OI → longs unwinding → bearish
        if is_oi_up and itype == 'CE' and strike > spot_price:
            summary['short_buildup'].append(
                {"strike": strike, "oi_chg_pct": round(oi_chg * 100, 1), "type": "CE"}
            )
        elif is_oi_up and itype == 'PE' and strike < spot_price:
            summary['long_buildup'].append(
                {"strike": strike, "oi_chg_pct": round(oi_chg * 100, 1), "type": "PE"}
            )
        elif is_oi_down and itype == 'CE' and strike > spot_price:
            summary['short_covering'].append(
                {"strike": strike, "oi_chg_pct": round(oi_chg * 100, 1), "type": "CE"}
            )
        elif is_oi_down and itype == 'PE' and strike < spot_price:
            summary['long_unwinding'].append(
                {"strike": strike, "oi_chg_pct": round(oi_chg * 100, 1), "type": "PE"}
            )

    # Derive net directional bias from buildup pattern
    bullish_signals = len(summary['long_buildup']) + len(summary['short_covering'])
    bearish_signals = len(summary['short_buildup']) + len(summary['long_unwinding'])

    if bullish_signals > bearish_signals + 2:
        bias = "bullish"
    elif bearish_signals > bullish_signals + 2:
        bias = "bearish"
    else:
        bias = "neutral"

    summary['buildup_bias']     = bias
    summary['bullish_signals']  = bullish_signals
    summary['bearish_signals']  = bearish_signals

    print(f"[OI BUILDUP] Bias: {bias} | Bull signals: {bullish_signals} | Bear signals: {bearish_signals}")
    return summary


# ─── STRIKE HEATMAP ────────────────────────────────────

def build_oi_heatmap(chain: pd.DataFrame, spot_price: float,
                     n: int = HEATMAP_STRIKES_EACH_SIDE) -> list[dict]:
    """
    Strike-wise OI heatmap — N strikes above and below spot.
    Each row: strike, ce_oi, pe_oi, pcr, ce_oi_chg%, pe_oi_chg%, ce_iv, pe_iv
    Sorted from highest to lowest strike for display.
    """
    if chain.empty or not spot_price:
        return []

    strikes = sorted(chain['strike'].unique())

    # ATM = nearest strike to spot
    atm     = min(strikes, key=lambda s: abs(s - spot_price))
    atm_idx = list(strikes).index(atm)

    low_idx  = max(0, atm_idx - n)
    high_idx = min(len(strikes), atm_idx + n + 1)
    window   = strikes[low_idx: high_idx]

    ce_df = chain[chain['instrument_type'] == 'CE'].set_index('strike')
    pe_df = chain[chain['instrument_type'] == 'PE'].set_index('strike')

    rows = []
    for s in reversed(window):   # top-down in terminal output
        ce = ce_df.loc[s] if s in ce_df.index else {}
        pe = pe_df.loc[s] if s in pe_df.index else {}

        ce_oi     = int(ce.get('oi', 0) or 0)
        pe_oi     = int(pe.get('oi', 0) or 0)
        ce_oi_chg = round((ce.get('oi_change_pct', 0) or 0) * 100, 1)
        pe_oi_chg = round((pe.get('oi_change_pct', 0) or 0) * 100, 1)
        ce_iv     = ce.get('iv')
        pe_iv     = pe.get('iv')
        pcr_s     = round(pe_oi / ce_oi, 2) if ce_oi > 0 else None
        is_atm    = (s == atm)

        rows.append({
            "strike":    s,
            "atm":       is_atm,
            "ce_oi":     ce_oi,
            "ce_oi_chg": ce_oi_chg,
            "ce_iv":     ce_iv,
            "pe_oi":     pe_oi,
            "pe_oi_chg": pe_oi_chg,
            "pe_iv":     pe_iv,
            "pcr":       pcr_s,
        })

    return rows


def print_heatmap(heatmap: list[dict]):
    """Pretty-print the OI heatmap to terminal."""
    if not heatmap:
        return

    header = (
        f"{'CE OI':>12}  {'CE Chg%':>8}  {'CE IV':>6}  "
        f"{'STRIKE':^8}  "
        f"{'PE IV':>6}  {'PE Chg%':>8}  {'PE OI':>12}  {'PCR':>6}"
    )
    print("\n" + "=" * len(header))
    print("  OPTION CHAIN OI HEATMAP")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for row in heatmap:
        atm_marker = " ◄ ATM" if row['atm'] else ""
        ce_oi_chg  = f"{row['ce_oi_chg']:+.1f}%" if row['ce_oi_chg'] else "  N/A"
        pe_oi_chg  = f"{row['pe_oi_chg']:+.1f}%" if row['pe_oi_chg'] else "  N/A"
        ce_iv      = f"{row['ce_iv']:.1f}"        if row['ce_iv']     else "  N/A"
        pe_iv      = f"{row['pe_iv']:.1f}"        if row['pe_iv']     else "  N/A"
        pcr        = f"{row['pcr']:.2f}"           if row['pcr']       else "  N/A"

        print(
            f"{row['ce_oi']:>12,}  {ce_oi_chg:>8}  {ce_iv:>6}  "
            f"{row['strike']:^8}  "
            f"{pe_iv:>6}  {pe_oi_chg:>8}  {row['pe_oi']:>12,}  {pcr:>6}"
            f"{atm_marker}"
        )

    print("=" * len(header) + "\n")


# ─── MASTER FUNCTION ───────────────────────────────────

def run_oi_analysis(kite, underlying_name: str, spot_price: float,
                    expiry_date=None) -> dict:
    """
    One-stop call: fetches full chain + computes all OI metrics.

    Returns a dict with keys:
        chain         → enriched DataFrame
        pcr           → PCR metrics dict
        max_pain      → max pain dict
        oi_walls      → support / resistance walls dict
        oi_buildup    → buildup / unwinding classification dict
        heatmap       → list of strike rows for display
        atm_strike    → nearest strike to spot
        expiry_used   → expiry date analysed
    """
    print(f"\n[OI ANALYSIS] {underlying_name} | Spot: {spot_price}")

    chain = fetch_option_chain(kite, underlying_name, expiry_date)
    if chain.empty:
        return {}

    expiry_used = chain['expiry'].iloc[0]
    chain       = enrich_with_quotes(kite, chain)

    strikes = sorted(chain['strike'].unique())
    atm     = min(strikes, key=lambda s: abs(s - spot_price))

    pcr        = compute_pcr(chain)
    max_pain   = compute_max_pain(chain)
    oi_walls   = find_oi_walls(chain, spot_price)
    oi_buildup = classify_oi_buildup(chain, spot_price)
    heatmap    = build_oi_heatmap(chain, spot_price)

    print_heatmap(heatmap)

    return {
        "chain":       chain,
        "pcr":         pcr,
        "max_pain":    max_pain,
        "oi_walls":    oi_walls,
        "oi_buildup":  oi_buildup,
        "heatmap":     heatmap,
        "atm_strike":  atm,
        "expiry_used": expiry_used,
    }


# ─── GEMINI SUMMARY FORMATTER ──────────────────────────

def format_oi_for_gemini(oi_result: dict, spot_price: float) -> str:
    """
    Returns a clean text block for injection into the Gemini prompt.
    Call this in gemini_brain.py and append to the prompt.
    """
    if not oi_result:
        return ""

    pcr     = oi_result.get('pcr', {})
    mp      = oi_result.get('max_pain', {})
    walls   = oi_result.get('oi_walls', {})
    buildup = oi_result.get('oi_buildup', {})

    resistance_list = ", ".join(
        str(w['strike']) for w in walls.get('resistance_walls', [])
    ) or "N/A"
    support_list = ", ".join(
        str(w['strike']) for w in walls.get('support_walls', [])
    ) or "N/A"

    lb = ", ".join(str(x['strike']) for x in buildup.get('long_buildup',   [])[:3]) or "None"
    sb = ", ".join(str(x['strike']) for x in buildup.get('short_buildup',  [])[:3]) or "None"
    sc = ", ".join(str(x['strike']) for x in buildup.get('short_covering', [])[:3]) or "None"
    lu = ", ".join(str(x['strike']) for x in buildup.get('long_unwinding', [])[:3]) or "None"

    return f"""
=== OPTION CHAIN OI ANALYSIS ===
PCR (OI)        : {pcr.get('pcr_oi', 'N/A')}  →  Sentiment: {pcr.get('sentiment', 'N/A')}
PCR (Volume)    : {pcr.get('pcr_volume', 'N/A')}
Total CE OI     : {pcr.get('total_ce_oi', 'N/A'):,}
Total PE OI     : {pcr.get('total_pe_oi', 'N/A'):,}

MAX PAIN STRIKE : {mp.get('max_pain_strike', 'N/A')}
  (Price is {'above' if spot_price > mp.get('max_pain_strike', spot_price) else 'below'} max pain — option writers prefer price to drift {'down' if spot_price > mp.get('max_pain_strike', spot_price) else 'up'} toward it)

OI WALLS (resistance — heavy CE writing):
  Strikes: {resistance_list}
  Nearest: {walls.get('nearest_resistance', 'N/A')}

OI WALLS (support — heavy PE writing):
  Strikes: {support_list}
  Nearest: {walls.get('nearest_support', 'N/A')}

OI BUILDUP / UNWINDING:
  Buildup Bias  : {buildup.get('buildup_bias', 'N/A')}
  Long Buildup  : {lb}   ← bulls adding longs (bullish)
  Short Buildup : {sb}   ← bears adding shorts (bearish pressure)
  Short Covering: {sc}   ← shorts exiting (bullish squeeze possible)
  Long Unwinding: {lu}   ← longs exiting (bearish)
"""
