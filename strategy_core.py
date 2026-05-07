from __future__ import annotations

from typing import Optional


EDGE_ID = "BULLISH_INTRADAY_MOMENTUM_CONTINUATION"
EDGE_DESCRIPTION = (
    "Buy CE only when intraday momentum is expanding in the direction of "
    "higher-timeframe bullish context, with enough room to the next resistance."
)


def evaluate_strategy_edge(indicators: dict, market_context: dict, regime: dict,
                           liquidity: Optional[dict] = None, oi_result: Optional[dict] = None,
                           direction: str = "CE") -> dict:
    direction = (direction or "CE").upper()
    edge_id = EDGE_ID if direction == "CE" else "BEARISH_INTRADAY_MOMENTUM_CONTINUATION"
    description = EDGE_DESCRIPTION if direction == "CE" else (
        "Buy PE only when intraday momentum is expanding in the direction of "
        "higher-timeframe bearish context, with enough room to the next support."
    )
    liquidity = liquidity or {}
    oi_result = oi_result or {}
    reasons = []
    warnings = []
    blocked = False
    block_reason = ""

    alignment = market_context.get("alignment")
    if direction == "CE" and alignment == "bullish":
        reasons.append("HTF context bullish")
    elif direction == "CE" and alignment == "bearish":
        blocked = True
        block_reason = "HTF context bearish against CE momentum edge"
    elif direction == "PE" and alignment == "bearish":
        reasons.append("HTF context bearish")
    elif direction == "PE" and alignment == "bullish":
        blocked = True
        block_reason = "HTF context bullish against PE momentum edge"
    else:
        warnings.append("HTF context mixed")

    if indicators.get("price_vs_vwap") == ("above" if direction == "CE" else "below"):
        reasons.append(f"price {'above' if direction == 'CE' else 'below'} VWAP")
    else:
        warnings.append("price on wrong side of VWAP")

    macd_hist = float(indicators.get("macd_hist") or 0)
    if indicators.get("macd_hist_growing") and (macd_hist > 0 if direction == "CE" else macd_hist < 0):
        reasons.append("MACD momentum expanding")
    else:
        warnings.append("MACD momentum not expanding")

    if liquidity.get("hard_block"):
        blocked = True
        block_reason = liquidity.get("block_reason", f"liquidity event blocks {direction}")

    if regime.get("hard_block"):
        blocked = True
        block_reason = regime.get("block_reason") or f"{regime.get('regime')} blocks {direction}"

    walls = oi_result.get("oi_walls", {})
    nearest_res = walls.get("nearest_resistance")
    spot = None
    daily = market_context.get("daily", {})
    if nearest_res and daily.get("position") == "above_pdh":
        reasons.append("spot above PDH with OI resistance still higher")

    supported_regimes = ("Trend Expansion", "Mean Reversion", "Opening Drive") if direction == "PE" else (
        "Trend Expansion",
        "Short Covering Rally",
        "Opening Drive",
    )
    supported = edge_id in regime.get("supports", []) or regime.get("regime") in supported_regimes
    if not supported:
        warnings.append(f"regime {regime.get('regime')} is not ideal for momentum continuation")

    return {
        "edge_id": edge_id,
        "description": description,
        "supported": supported and not blocked,
        "blocked": blocked,
        "block_reason": block_reason,
        "warnings": warnings,
        "reasons": reasons,
    }
