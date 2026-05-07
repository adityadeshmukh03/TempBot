# gemini_brain.py
#
# GEMINI'S ROLE IN THIS SYSTEM â€” READ THIS BEFORE TOUCHING ANYTHING
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Python's job : compute indicators, count confluence, manage OI math.
#                Python is the ENTRY ENGINE. It finds reasons to trade.
#
# Gemini's job : be the ADVERSARY. Find every reason this trade is wrong.
#                Gemini does NOT confirm trades. It only downgrades or kills them.
#
# Mental model : Python is an optimistic analyst who sees the setup.
#                Gemini is the risk manager saying "have you considered
#                that this might be a trap?"
#
# Signal flow  :
#   Python confluence met  â†’  Gemini audit  â†’  final_signal() decides
#        â†“                         â†“                    â†“
#   ENTER (provisional)    PASS / WARN / VETO    ENTER / WAIT
#
# Gemini CAN : downgrade confidence, raise confluence bar, kill the trade.
# Gemini CANNOT : produce an ENTER, confirm a signal, compute trade targets.
#
# Trade levels (entry, SL, targets) are computed by Python only.
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

import google.generativeai as genai
import concurrent.futures
import json
import config

genai.configure(api_key=config.GEMINI_API_KEY)

# Flash is the primary model â€” fast enough for 5-min candles (~2-4s response).
# Pro is used as a fallback when Flash returns invalid JSON or an API error,
# and for WARN re-audits where deeper reasoning is worth the extra latency.
_model_flash = genai.GenerativeModel("gemini-2.0-flash")
_model_pro   = genai.GenerativeModel("gemini-2.5-pro")


def _generate_with_timeout(model, prompt, timeout_seconds, label):
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(model.generate_content, prompt)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise TimeoutError(f"{label} timed out after {timeout_seconds}s")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


# â”€â”€â”€ WHAT GEMINI AUDITS FOR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Each item is a reason NOT to trade. Never a reason to enter.

AUDIT_CHECKLIST = """
CANDLE STRUCTURE FAILURES:
  - Exhaustion        : Bodies shrinking after a sustained move, last candle wick-heavy
  - Rejection_Wick    : Upper wick > 60% of total range on the most recent candle
  - Doji_At_High      : Doji at intraday high or PDH â€” indecision at resistance
  - Volume_Divergence : Price moving up, volume shrinking candle-over-candle
  - Bear_Engulf       : Bearish candle body fully covers prior bullish body
  - Inside_Bar_Chop   : 3+ consecutive inside bars â€” no directional conviction

STRUCTURAL / LEVEL FAILURES:
  - PDH_Proximity     : Price within 0.3% of PDH or Intraday High with weakening momentum
  - OI_Wall_Proximity : Price approaching known CE writing strike (OI wall)
  - Max_Pain_Distance : Spot >1.5% above max pain with DTE < 3 â€” gravity pulling down
  - VWAP_Failure      : Price reclaimed VWAP but now losing it again â€” false reclaim

OPTION-SPECIFIC FAILURES:
  - IV_Crush_Risk     : IV direction "rising" AND IV Rank > 60
  - Theta_Bleed       : DTE = 0 and time after 2:00 PM
  - Low_Delta_Noise   : Delta < 0.25 â€” option barely moves with underlying
  - PCR_Against       : PCR > 1.3 with bearish OI buildup bias

CONTEXT FAILURES:
  - Bull_Trap_Context : Prior candle broke a key level, current candle back below it
  - Chop_Context      : Alternating bull/bear candles, no consistent direction
"""

AUDIT_CHECKLIST_PE = """
CANDLE STRUCTURE FAILURES (PE):
  - Exhaustion_Bear    : Bodies shrinking after sustained decline, last candle wick-heavy on bottom
  - Lower_Wick_Reject  : Lower wick > 60% of total range - buyers absorbing, PE rejection likely
  - Doji_At_Low        : Doji at intraday low or PDL - indecision at support
  - Volume_Divergence  : Price moving down, volume shrinking - weak continuation
  - Bull_Engulf        : Bullish candle body fully covers prior bearish body
  - Inside_Bar_Chop    : 3+ consecutive inside bars - no directional conviction

STRUCTURAL / LEVEL FAILURES (PE):
  - PDL_Proximity      : Price within 0.3% of PDL or Intraday Low with weakening momentum
  - PE_Wall_Proximity  : Price approaching known PE writing strike (OI support wall)
  - Max_Pain_Distance  : Spot >1.5% below max pain with DTE < 3 - gravity pulling up
  - VWAP_Failure       : Price lost VWAP but now reclaiming it - false breakdown

OPTION-SPECIFIC FAILURES (PE):
  - IV_Crush_Risk      : IV direction rising AND IV Rank > 60
  - Theta_Bleed        : DTE = 0 and time after 2:00 PM
  - Low_Delta_Noise    : Delta < 0.25 (absolute) - PE barely moves with underlying
  - PCR_Against        : PCR < 0.7 with bullish OI buildup bias

CONTEXT FAILURES (PE):
  - Bear_Trap_Context  : Prior candle broke a key support, current candle back above it
  - Chop_Context       : Alternating bull/bear candles, no consistent direction
"""


# â”€â”€â”€ TRADE LEVELS â€” PYTHON ONLY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def compute_trade_levels(indicators, key_levels, recent_candles,
                         underlying_spot=None, direction: str = "CE"):
    """
    Compute option-buyer trade levels from structure.

    Option-price behaviour is the same for CE and PE buyers: stop below entry,
    targets above entry. Direction changes the underlying structural reference:
    CE sizes risk from swing low and caps target_2 near resistance; PE sizes risk
    from swing high and caps target_2 near support.
    """
    direction = (direction or "CE").upper()
    option_price = indicators['current_price']
    spot = underlying_spot or option_price

    if recent_candles:
        if direction == "PE":
            option_swing_high = max(c['high'] for c in recent_candles[-4:])
            if option_price > 0:
                ratio = option_swing_high / option_price
                underlying_swing_high = spot * ratio
            else:
                underlying_swing_high = spot * 1.01
            underlying_sl_distance = max(underlying_swing_high - spot, 0)
        else:
            option_swing_low = min(c['low'] for c in recent_candles[-4:])
            if option_price > 0:
                ratio = option_swing_low / option_price
                underlying_swing_low = spot * ratio
            else:
                underlying_swing_low = spot * 0.99
            underlying_sl_distance = max(spot - underlying_swing_low, 0)
    else:
        underlying_sl_distance = spot * 0.005

    delta = abs(indicators.get('opt_delta') or 0.5)
    delta = max(delta, 0.10)

    option_sl_distance = underlying_sl_distance * delta
    max_sl_distance = option_price * 0.015
    sl_distance = min(option_sl_distance, max_sl_distance)
    sl_distance = max(sl_distance, 1.0)

    stop_loss = round(option_price - sl_distance, 2)
    target_1 = round(option_price + sl_distance * 1.5, 2)
    target_2 = round(option_price + sl_distance * 2.5, 2)

    if direction == "PE":
        floors_underlying = []
        if key_levels.get('PDL') and key_levels['PDL'] < spot:
            floors_underlying.append(key_levels['PDL'])
        if indicators['intraday_low'] < spot:
            floors_underlying.append(indicators['intraday_low'])

        if floors_underlying:
            nearest_floor_underlying = max(floors_underlying)
            room_to_floor_underlying = spot - nearest_floor_underlying
            room_to_floor_option = room_to_floor_underlying * delta
            floor_in_option_space = option_price + room_to_floor_option
            if target_2 > floor_in_option_space - 5:
                target_2 = round(floor_in_option_space - 5, 2)
    else:
        ceilings_underlying = []
        if key_levels.get('PDH') and key_levels['PDH'] > spot:
            ceilings_underlying.append(key_levels['PDH'])
        if indicators['intraday_high'] > spot:
            ceilings_underlying.append(indicators['intraday_high'])

        if ceilings_underlying:
            nearest_ceiling_underlying = min(ceilings_underlying)
            room_to_ceiling_underlying = nearest_ceiling_underlying - spot
            room_to_ceiling_option = room_to_ceiling_underlying * delta
            ceiling_in_option_space = option_price + room_to_ceiling_option
            if target_2 > ceiling_in_option_space - 5:
                target_2 = round(ceiling_in_option_space - 5, 2)

    if target_2 <= target_1:
        target_2 = target_1

    return {
        "entry_price": option_price,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "risk_points": round(sl_distance, 2),
    }

# â”€â”€â”€ SECTION FORMATTERS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _fmt(v, default="N/A"):
    return v if v is not None else default


def _format_candles_for_audit(recent_candles):
    """Pre-compute per-candle structural features so Gemini reasons from facts."""
    enriched = []
    prev_vol  = None
    for i, c in enumerate(recent_candles):
        body        = abs(c['close'] - c['open'])
        upper_wick  = c['high'] - max(c['close'], c['open'])
        lower_wick  = min(c['close'], c['open']) - c['low']
        total_range = c['high'] - c['low']
        body_pct    = round(body / total_range * 100, 1) if total_range > 0 else 0
        upper_wick_pct = round(upper_wick / total_range * 100, 1) if total_range > 0 else 0

        vol_vs_prev = None
        if prev_vol and prev_vol > 0:
            ratio = c['volume'] / prev_vol
            vol_vs_prev = "higher" if ratio > 1.1 else "lower" if ratio < 0.9 else "similar"
        prev_vol = c['volume']

        enriched.append({
            "n":              i + 1,
            "time":           str(c['time']),
            "open":           c['open'],
            "high":           c['high'],
            "low":            c['low'],
            "close":          c['close'],
            "volume":         c['volume'],
            "direction":      "bullish" if c['close'] > c['open'] else
                              "bearish" if c['close'] < c['open'] else "doji",
            "body_pct":       body_pct,
            "upper_wick_pct": upper_wick_pct,
            "upper_wick":     round(upper_wick, 2),
            "lower_wick":     round(lower_wick, 2),
            "body_size":      round(body, 2),
            "vol_vs_prev":    vol_vs_prev,
        })
    return enriched


def _format_option_section(indicators):
    opt = {k[4:]: v for k, v in indicators.items() if k.startswith('opt_')}
    if not opt or all(v is None for v in opt.values()):
        return ""
    return f"""
=== OPTION GREEKS ===
IV / IV Rank / Percentile : {_fmt(opt.get('iv'))} / {_fmt(opt.get('iv_rank'))} / {_fmt(opt.get('iv_percentile'))}
IV Direction              : {_fmt(opt.get('iv_direction'))}
Delta / Gamma / Theta     : {_fmt(opt.get('delta'))} / {_fmt(opt.get('gamma'))} / {_fmt(opt.get('theta'))}
Days to Expiry            : {_fmt(opt.get('days_to_expiry'))}
OI / OI Change            : {_fmt(opt.get('oi'))} / {_fmt(opt.get('oi_change'))}
"""


def _format_oi_chain_section(oi_result):
    if not oi_result:
        return ""
    pcr     = oi_result.get('pcr', {})
    mp      = oi_result.get('max_pain', {})
    walls   = oi_result.get('oi_walls', {})
    buildup = oi_result.get('oi_buildup', {})
    res     = ", ".join(str(w['strike']) for w in walls.get('resistance_walls', [])[:3]) or "N/A"
    return f"""
=== OPTION CHAIN CONTEXT ===
PCR (OI / Vol)      : {_fmt(pcr.get('pcr_oi'))} / {_fmt(pcr.get('pcr_volume'))} â†’ {_fmt(pcr.get('sentiment'))}
Max Pain Strike     : {_fmt(mp.get('max_pain_strike'))}
Nearest CE Wall     : {_fmt(walls.get('nearest_resistance'))}  (top walls: {res})
Nearest PE Support  : {_fmt(walls.get('nearest_support'))}
OI Buildup Bias     : {_fmt(buildup.get('buildup_bias'))}
"""


# â”€â”€â”€ GEMINI AUDIT PROMPT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _format_market_context_section(market_context):
    if not market_context:
        return ""
    return f"""
=== HIGHER TIMEFRAME CONTEXT ===
Alignment       : {_fmt(market_context.get('alignment'))} (score={_fmt(market_context.get('alignment_score'))})
15m / 1h Trend  : {_fmt(market_context.get('htf', {}).get('15m', {}).get('trend'))} / {_fmt(market_context.get('htf', {}).get('60m', {}).get('trend'))}
Daily Structure : {_fmt(market_context.get('daily', {}).get('position'))}
Gap Context     : {_fmt(market_context.get('daily', {}).get('gap', {}).get('type'))} ({_fmt(market_context.get('daily', {}).get('gap', {}).get('pct'))}%)
Opening Range   : {_fmt(market_context.get('opening_range', {}).get('bias'))}
"""


def _format_regime_section(regime_result, liquidity):
    if not regime_result and not liquidity:
        return ""
    return f"""
=== REGIME / LIQUIDITY CONTEXT ===
Regime          : {_fmt((regime_result or {}).get('regime'))} ({_fmt((regime_result or {}).get('confidence'))})
Regime Evidence : {'; '.join((regime_result or {}).get('evidence', [])[:3]) or 'N/A'}
Liquidity Bias  : {_fmt((liquidity or {}).get('bias'))}
Liquidity Events: {', '.join((liquidity or {}).get('events', [])) or 'None'}
"""


def build_audit_prompt(indicators, key_levels, recent_candles, option_info,
                        condition, oi_result=None, market_context=None,
                        regime_result=None, liquidity=None, direction: str = "CE"):
    """
    Gemini receives a provisional ENTER signal and must find reasons to kill it.
    It does NOT see whether Python's confluence was met or what the score was.
    It only sees raw market data. No bias hints. No permission to confirm.
    """
    direction        = (direction or "CE").upper()
    price            = indicators['current_price']
    underlying_spot  = option_info.get('underlying_spot')
    enriched_candles = _format_candles_for_audit(recent_candles)
    option_section   = _format_option_section(indicators)
    oi_section       = _format_oi_chain_section(oi_result)
    context_section  = _format_market_context_section(market_context)
    regime_section   = _format_regime_section(regime_result, liquidity)
    n                = len(enriched_candles)
    checklist        = AUDIT_CHECKLIST_PE if direction == "PE" else AUDIT_CHECKLIST
    direction_label  = "PE (bearish)" if direction == "PE" else "CE (bullish)"
    step_2_title     = "SUPPORT PROXIMITY" if direction == "PE" else "RESISTANCE PROXIMITY"
    step_2_level     = "PDL/IntradayLow" if direction == "PE" else "PDH/IntradayHigh"
    step_2_wall      = "PE OI wall" if direction == "PE" else "CE OI wall"
    step_2_quality   = "Weak lower-wick reaction at support = rejection likely." if direction == "PE" else "Weak wick approach to resistance = rejection likely."
    max_pain_question = "Is spot below max pain? By how much? What is DTE?" if direction == "PE" else "Is spot above max pain? By how much? What is DTE?"
    pcr_check        = "PCR < 0.7 with bullish OI buildup? (smart money against you)" if direction == "PE" else "PCR > 1.3 with bearish OI buildup? (smart money against you)"
    candle_focus     = (
        "lower wick rejection, bull engulf, and bodies shrinking on the downside"
        if direction == "PE"
        else "upper wick rejection, bear engulf, and bodies shrinking on the upside"
    )

    # CRITICAL: level_distances must use underlying_spot, NOT option LTP.
    # PDH, PDL, PDC, IntradayHigh, IntradayLow, VWAP are all underlying prices.
    # Comparing option premium to these values is meaningless for structural analysis.
    ref_price = underlying_spot or price   # use spot; fall back to option LTP only if unavailable
    level_distances = {}
    for name, level in [
        ("PDH",          key_levels.get('PDH')),
        ("PDL",          key_levels.get('PDL')),
        ("PDC",          key_levels.get('PDC')),
        ("IntradayHigh", indicators['intraday_high']),
        ("IntradayLow",  indicators['intraday_low']),
        ("VWAP",         indicators['vwap']),
    ]:
        if level:
            dist = round(ref_price - level, 2)
            pct  = round(dist / level * 100, 3)
            level_distances[name] = {
                "level":    level,
                "distance": dist,
                "pct_away": pct,
                "position": "above" if dist > 0 else "below"
            }

    prompt = f"""
You are a risk auditor for an algorithmic options trading system.
A Python system has flagged a potential {direction_label} entry signal.
Your job: find every structural reason this trade might fail.

You are NOT here to say the setup looks good.
You are NOT here to confirm anything.
You are the adversary. Look for problems. If you find none, say so â€” but look hard first.

=== TRADE BEING AUDITED ===
Instrument     : {option_info['symbol']}
Option LTP     : {option_info['ltp']}
Underlying Spot: {_fmt(underlying_spot)}
Market Regime  : {condition}

=== UNDERLYING SPOT DISTANCES FROM KEY LEVELS ===
(All distances computed using underlying spot price â€” NOT option premium)
{json.dumps(level_distances, indent=2)}

=== INDICATOR CONTEXT (background only â€” do not re-score) ===
EMA 9 / 21 / 50 : {indicators['ema9']} / {indicators['ema21']} / {_fmt(indicators.get('ema50'))}
ADX             : {indicators['adx']}  ({'trending' if indicators['adx'] > 25 else 'ranging/mixed'})
RSI             : {indicators['rsi']}
MACD Histogram  : {indicators['macd_hist']}  ({'growing' if indicators['macd_hist_growing'] else 'shrinking'})
Stoch K / D     : {indicators['stoch_k']} / {indicators['stoch_d']}
Volume Spike    : {indicators['volume_spike']}
OBV Rising      : {indicators['obv_rising']}
{option_section}
{oi_section}
{context_section}
{regime_section}

=== LAST {n} CANDLES â€” YOUR PRIMARY EVIDENCE ===
(n=1 is oldest, n={n} is most recent)
{json.dumps(enriched_candles, indent=2)}

=== FAILURE MODES TO CHECK ===
{checklist}

=== YOUR AUDIT â€” DO THESE IN ORDER ===

STEP 1 â€” CANDLE SEQUENCE
Look at candles in order. For {direction}, focus on {candle_focus}. Answer:
  a) Is candle n={n} (most recent) body_pct > 60%? Or is it wick-heavy (body_pct < 40%)?
  b) Is candle n={n}'s volume higher or lower than candle n={n-1}?
  c) Across candles n=1 to n={n}: are bodies generally getting bigger or smaller?
  d) Did any candle make a lower low than the one before it? Which one?

STEP 2 â€” {step_2_title}
  a) Is underlying spot within 0.3% of {step_2_level}?
  b) Is underlying spot close to a {step_2_wall}?
  c) If yes: is candle n={n} approaching that level with a strong body or a weak wick?
     {step_2_quality}
  d) {max_pain_question}

STEP 3 â€” OPTION RISK
  a) Is IV Rank > 60? Is IV direction rising? (both = expensive entry)
  b) Is DTE 0 after 2PM? (theta destroying premium)
  c) Is delta < 0.25? (barely captures the underlying move)
  d) {pcr_check}

STEP 4 â€” VERDICT
Choose exactly one:

  "PASS"  â€” You checked every item and found no significant problem.
             State what you checked and did NOT find. Do not use positive language.
             This is not approval â€” it means you found no objection.

  "WARN"  â€” You found 1-2 concerns but nothing definitively fatal.
             The trade may proceed with higher confidence bar.
             Describe each concern specifically (which candle, which level, which metric).

  "VETO"  â€” You found a clear reason this trade should not happen right now.
             One of these must be true:
               - Rejection wick at resistance (cite candle n=X, upper_wick_pct value)
               - Bear engulf visible (cite candle numbers)
               - Price failing VWAP reclaim (describe the candle structure)
               - IV Rank > 75 with rising IV (cite the values)
               - DTE 0 after 2PM (state the time)
             Vague vetoes are not accepted. Cite specific data.

Respond ONLY in this exact JSON, nothing else:
{{
  "candle_read": {{
    "last_candle_strong":  true or false,
    "volume_confirming":   true or false,
    "momentum_direction":  "building / exhausting / flat",
    "any_lower_low":       true or false,
    "lower_low_candle":    0
  }},
  "level_risks": {{
    "near_resistance":   true or false,
    "resistance_name":   "PDH / IntradayHigh / OI_Wall / None",
    "resistance_level":  0,
    "approach_quality":  "strong / weak / N/A",
    "above_max_pain":    true or false,
    "max_pain_risk":     true or false
  }},
  "option_risks": {{
    "iv_expensive":          true or false,
    "iv_rising_into_entry":  true or false,
    "theta_bleed_risk":      true or false,
    "low_delta":             true or false,
    "pcr_against":           true or false
  }},
  "flags_found":    ["exact checklist item names triggered â€” empty list if none"],
  "audit_verdict":  "PASS / WARN / VETO",
  "warn_reasons":   "only if WARN â€” each concern on one line",
  "veto_reason":    "only if VETO â€” candle number + feature + value that triggered it",
  "what_i_checked": "what audit items you examined and found clean (2 sentences max)"
}}
"""
    return prompt


# â”€â”€â”€ FINAL SIGNAL RECONCILIATION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def final_signal(confluence: dict, audit: dict, trade_levels: dict) -> dict:
    """
    Gate rules, in priority order:
      1. VETO  â†’ always WAIT
      2. WARN  â†’ require +1 confluence point
      3. Confluence gate â†’ WAIT if not met
      4. PASS + confluence met â†’ ENTER

    Confidence scoring:
      margin = score - effective_required
      margin 0   â†’ medium
      margin >= 2 â†’ high
      any option risk flag â†’ downgrade one level
    """
    c_score  = confluence['score']
    c_req    = confluence['required']
    verdict  = audit.get('audit_verdict', 'VETO')
    opt_risk = audit.get('option_risks', {})
    flags    = audit.get('flags_found', [])

    if confluence.get('oi_block'):
        return {
            "signal":        "WAIT",
            "confidence":    "low",
            "reason":        f"OI_BLOCK - {confluence.get('oi_block_reason', 'OI hard block')}",
            "downgraded_by": "oi_block",
            "entry_price": 0, "stop_loss": 0, "target_1": 0, "target_2": 0,
        }

    # â”€â”€ 1. VETO â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if verdict == "VETO":
        return {
            "signal":        "WAIT",
            "confidence":    "low",
            "reason":        f"AUDIT_VETO â€” {audit.get('veto_reason', 'structural issue')}",
            "downgraded_by": "gemini_veto",
            "entry_price": 0, "stop_loss": 0, "target_1": 0, "target_2": 0,
        }

    # â”€â”€ 2. WARN raises bar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    effective_required = c_req + (1 if verdict == "WARN" else 0)

    # â”€â”€ 3. Confluence gate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if c_score < effective_required or not confluence.get('met', False):
        note = f" (bar raised to {effective_required} â€” WARN active)" if verdict == "WARN" else ""
        return {
            "signal":        "WAIT",
            "confidence":    "low",
            "reason":        f"Confluence {c_score}/{c_req} not sufficient{note}",
            "downgraded_by": "gemini_warn" if verdict == "WARN" else None,
            "entry_price": 0, "stop_loss": 0, "target_1": 0, "target_2": 0,
        }

    # â”€â”€ 4. ENTER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    margin     = c_score - effective_required
    confidence = "high" if margin >= 2 else "medium"

    option_landmine = (
        (opt_risk.get('iv_expensive') and opt_risk.get('iv_rising_into_entry')) or
        opt_risk.get('theta_bleed_risk') or
        opt_risk.get('low_delta')
    )
    if option_landmine:
        confidence = "medium" if confidence == "high" else "low"

    flag_note = f" | Flags: {', '.join(flags)}" if flags else ""
    warn_note = f" | {audit.get('warn_reasons', '')}" if verdict == "WARN" else ""

    return {
        "signal":        "ENTER",
        "confidence":    confidence,
        "reason":        f"Confluence {c_score}/{c_req} | Audit: {verdict}{warn_note}{flag_note}",
        "downgraded_by": "gemini_warn" if verdict == "WARN" else None,
        **trade_levels,
    }


# â”€â”€â”€ PUBLIC API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def analyse(indicators, key_levels, recent_candles, option_info,
            condition, confluence, oi_result=None, market_context=None,
            regime_result=None, liquidity=None, direction: str = "CE"):
    """
    Three-step pipeline:
      1. compute_trade_levels() â€” Python calculates SL and targets from structure
      2. Gemini audit           â€” adversarial review of the provisional entry
      3. final_signal()         â€” gate rules produce the final unified output
    """

    # Step 1 â€” Python computes levels, before Gemini is involved
    trade_levels = compute_trade_levels(indicators, key_levels, recent_candles,
                                           underlying_spot=option_info.get("underlying_spot"),
                                           direction=direction)

    # Step 2 â€” Gemini audit
    prompt = build_audit_prompt(
        indicators, key_levels, recent_candles, option_info,
        condition, oi_result=oi_result, market_context=market_context,
        regime_result=regime_result, liquidity=liquidity, direction=direction
    )

    # Initialise raw here so it's always defined in the except blocks
    raw          = ""
    audit_result = {}

    def _parse_response(response):
        """Strip markdown fences and parse JSON from a model response."""
        text = response.text.strip().replace("```json", "").replace("```", "").strip()
        return text, json.loads(text)

    # â”€â”€ Primary call: Flash (~2-4s, sufficient for 5-min candles) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    flash_ok = False
    try:
        print("[GEMINI] Calling Flash model for audit...")
        response     = _generate_with_timeout(
            _model_flash,
            prompt,
            getattr(config, "GEMINI_FLASH_TIMEOUT_SECONDS", 5),
            "Gemini Flash"
        )
        raw, audit_result = _parse_response(response)
        flash_ok     = True
        print("[GEMINI] Flash audit complete.")

    except json.JSONDecodeError:
        print(f"[GEMINI] Flash returned invalid JSON â€” falling back to Pro.\n{raw}")

    except Exception as e:
        print(f"[GEMINI] Flash API error ({e}) â€” falling back to Pro.")

    # â”€â”€ Fallback: Pro â€” used when Flash fails OR verdict is WARN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # WARN triggers a Pro re-audit because the trade is borderline and the
    # deeper reasoning is worth the extra ~10-20s at that decision point.
    needs_pro = (not flash_ok) or (audit_result.get("audit_verdict") == "WARN")

    if needs_pro:
        reason = "WARN re-audit" if flash_ok else "Flash failure fallback"
        print(f"[GEMINI] Calling Pro model ({reason})...")
        try:
            response     = _generate_with_timeout(
                _model_pro,
                prompt,
                getattr(config, "GEMINI_PRO_TIMEOUT_SECONDS", 8),
                "Gemini Pro"
            )
            raw, audit_result = _parse_response(response)
            print("[GEMINI] Pro audit complete.")

        except json.JSONDecodeError:
            print(f"[ERROR] Pro also returned invalid JSON:\n{raw}")
            audit_result = {
                "audit_verdict": "VETO",
                "flags_found": ["Gemini_Audit_Unavailable"],
                "option_risks": {},
                "veto_reason": "Both Flash and Pro returned invalid JSON â€” fail closed",
                "what_i_checked": "Gemini audit unavailable; trade blocked by fail-closed policy.",
            }

        except Exception as e:
            print(f"[ERROR] Pro API error: {e}")
            audit_result = {
                "audit_verdict": "VETO",
                "flags_found": ["Gemini_Audit_Unavailable"],
                "option_risks": {},
                "veto_reason": f"Gemini Pro API error â€” fail closed: {e}",
                "what_i_checked": "Gemini audit unavailable; trade blocked by fail-closed policy.",
            }

    # Step 3 â€” gate
    if audit_result.get("audit_verdict") not in ("PASS", "WARN", "VETO"):
        audit_result = {
            "audit_verdict": "VETO",
            "flags_found": ["Gemini_Audit_Invalid"],
            "option_risks": {},
            "veto_reason": "Gemini audit missing a valid verdict - fail closed",
            "what_i_checked": "Gemini audit output was incomplete; trade blocked by fail-closed policy.",
        }

    decision = final_signal(confluence, audit_result, trade_levels)

    return {
        # Core decision
        "signal":          decision["signal"],
        "confidence":      decision["confidence"],
        "decision_reason": decision["reason"],
        "downgraded_by":   decision.get("downgraded_by"),

        # Python
        "condition":          condition,
        "regime":             (regime_result or {}).get("regime", ""),
        "regime_confidence":  (regime_result or {}).get("confidence", ""),
        "market_alignment":   (market_context or {}).get("alignment", ""),
        "liquidity_bias":     (liquidity or {}).get("bias", ""),
        "liquidity_events":   (liquidity or {}).get("events", []),
        "confluence_score":   confluence["score"],
        "confluence_required": confluence["required"],
        "confluence_met":     confluence["met"],
        "confluence_reasons": confluence["reasons"],
        "confluence_missed":  confluence["missed"],
        "underlying_spot":    option_info.get("underlying_spot"),

        # Trade levels (Python-computed)
        "entry_price": decision.get("entry_price", 0),
        "stop_loss":   decision.get("stop_loss",   0),
        "target_1":    decision.get("target_1",    0),
        "target_2":    decision.get("target_2",    0),
        "risk_points": trade_levels.get("risk_points", 0),

        # Gemini audit
        "audit_verdict":        audit_result.get("audit_verdict",     "N/A"),
        "audit_flags":          audit_result.get("flags_found",       []),
        "veto_reason":          audit_result.get("veto_reason",       ""),
        "warn_reasons":         audit_result.get("warn_reasons",       ""),
        "what_auditor_checked": audit_result.get("what_i_checked",    ""),
        "near_resistance":      audit_result.get("level_risks", {}).get("near_resistance",  False),
        "resistance_level":     audit_result.get("level_risks", {}).get("resistance_level", 0),
        "candle_momentum":      audit_result.get("candle_read", {}).get("momentum_direction", "N/A"),
        "volume_confirms":      audit_result.get("candle_read", {}).get("volume_confirming",  False),
        "option_risk_flags":    [k for k, v in audit_result.get("option_risks", {}).items() if v],
    }
