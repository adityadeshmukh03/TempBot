# position_sizing.py
#
# Risk-based position sizing for options.
#
# Core formula:
#   risk_amount  = TOTAL_CAPITAL * RISK_PER_TRADE_PCT / 100
#   sl_distance  = entry_price - stop_loss   (points per unit of option)
#   raw_units    = risk_amount / sl_distance
#   lots         = floor(raw_units / LOT_SIZE)
#   lots         = clamp(lots, 1, MAX_LOTS)
#
# Edge cases handled:
#   - SL distance = 0 or negative → return 0 lots, block trade
#   - Calculated lots = 0         → return 0 lots, block trade
#   - Max lots cap always enforced
# ────────────────────────────────────────────────────────

import math
import config


def calculate_position_size(
    entry_price:  float,
    stop_loss:    float,
    capital:      float = None,
    risk_pct:     float = None,
    lot_size:     int   = None,
    max_lots:     int   = None,
) -> dict:
    """
    Compute how many lots to trade given a stop loss.

    Parameters
    ----------
    entry_price : float — option entry price (LTP at signal)
    stop_loss   : float — option stop loss price
    capital     : float — override config.TOTAL_CAPITAL
    risk_pct    : float — override config.RISK_PER_TRADE_PCT
    lot_size    : int   — override config.LOT_SIZE
    max_lots    : int   — override config.MAX_LOTS

    Returns
    -------
    dict with:
        lots          — number of lots to trade (0 = do not trade)
        units         — lots * lot_size (total option contracts)
        risk_amount   — ₹ being risked (before brokerage)
        max_loss      — worst case ₹ loss if SL is hit exactly
        capital_used  — approximate notional (entry * units)
        sl_distance   — stop distance in points
        risk_pct_used — actual % of capital being risked
        blocked       — True if size calculation says no trade
        block_reason  — why blocked (empty string if not blocked)
    """
    # Resolve parameters
    capital   = capital  or config.TOTAL_CAPITAL
    risk_pct  = risk_pct or config.RISK_PER_TRADE_PCT
    lot_size  = lot_size or config.LOT_SIZE
    max_lots  = max_lots or config.MAX_LOTS

    result = {
        "lots":          0,
        "units":         0,
        "risk_amount":   0.0,
        "max_loss":      0.0,
        "capital_used":  0.0,
        "sl_distance":   0.0,
        "risk_pct_used": 0.0,
        "blocked":       True,
        "block_reason":  "",
    }

    # Validate inputs
    sl_distance = round(entry_price - stop_loss, 2)

    if sl_distance <= 0:
        result["block_reason"] = (
            f"Invalid SL: entry {entry_price} <= stop {stop_loss} — "
            f"SL must be below entry (option price always drops when trade goes wrong)"
        )
        return result

    if entry_price <= 0:
        result["block_reason"] = f"Entry price {entry_price} is invalid"
        return result

    # Core calculation
    risk_amount = capital * risk_pct / 100.0
    raw_units   = risk_amount / sl_distance
    raw_lots    = raw_units / lot_size

    lots = max(1, math.floor(raw_lots))   # at least 1 lot if we're trading
    lots = min(lots, max_lots)            # never exceed cap

    # Sanity: can we actually afford 1 lot?
    min_capital_needed = entry_price * lot_size
    if min_capital_needed > capital * 0.5:
        result["block_reason"] = (
            f"1 lot costs ₹{min_capital_needed:,.0f} — "
            f"exceeds 50% of capital ₹{capital:,.0f}"
        )
        return result

    units        = lots * lot_size
    actual_risk  = sl_distance * units
    capital_used = entry_price * units
    risk_pct_actual = actual_risk / capital * 100

    result.update({
        "lots":          lots,
        "units":         units,
        "risk_amount":   round(risk_amount, 2),
        "max_loss":      round(actual_risk, 2),
        "capital_used":  round(capital_used, 2),
        "sl_distance":   sl_distance,
        "risk_pct_used": round(risk_pct_actual, 3),
        "blocked":       False,
        "block_reason":  "",
    })
    return result


def format_sizing_summary(sizing: dict) -> str:
    """One-line terminal summary of position sizing output."""
    if sizing["blocked"]:
        return f"[SIZING] BLOCKED — {sizing['block_reason']}"
    return (
        f"[SIZING] {sizing['lots']} lot(s) × {sizing['units']} units | "
        f"SL dist: {sizing['sl_distance']} pts | "
        f"Max loss: ₹{sizing['max_loss']:,.0f} | "
        f"Risk: {sizing['risk_pct_used']:.2f}% of capital | "
        f"Capital used: ₹{sizing['capital_used']:,.0f}"
    )
