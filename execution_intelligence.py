from __future__ import annotations

from typing import Optional
import config


def analyse_execution_quality(option_data: Optional[dict], desired_side: str = "BUY") -> dict:
    option_data = option_data or {}
    ltp = option_data.get("ltp")
    bid = option_data.get("best_bid")
    ask = option_data.get("best_ask")
    bid_qty = option_data.get("best_bid_qty") or 0
    ask_qty = option_data.get("best_ask_qty") or 0
    volume = option_data.get("volume") or 0
    oi = option_data.get("oi") or 0

    result = {
        "available": False,
        "quality": "unknown",
        "blocked": False,
        "block_reason": "",
        "spread": None,
        "spread_pct": None,
        "estimated_slippage": 0,
        "suggested_entry": ltp or 0,
        "notes": [],
    }

    if not ltp:
        result["blocked"] = True
        result["block_reason"] = "missing option LTP"
        return result

    if not bid or not ask or ask <= bid:
        result["notes"].append("depth unavailable or stale; using LTP as entry")
        result["quality"] = "unknown"
        return result

    spread = ask - bid
    spread_pct = spread / ltp * 100
    imbalance = (bid_qty - ask_qty) / max(bid_qty + ask_qty, 1)
    estimated_slippage = max(spread / 2, ltp * 0.001)
    suggested_entry = ask if desired_side == "BUY" else bid

    result.update({
        "available": True,
        "spread": round(spread, 2),
        "spread_pct": round(spread_pct, 3),
        "estimated_slippage": round(estimated_slippage, 2),
        "suggested_entry": round(suggested_entry, 2),
        "depth_imbalance": round(imbalance, 3),
    })

    max_spread = getattr(config, "EXECUTION_MAX_SPREAD_PCT", 2.0)
    caution_spread = getattr(config, "EXECUTION_CAUTION_SPREAD_PCT", 1.0)
    min_volume = getattr(config, "EXECUTION_MIN_VOLUME", 1000)
    min_oi = getattr(config, "EXECUTION_MIN_OI", 10000)

    if spread_pct > max_spread:
        result["blocked"] = True
        result["block_reason"] = f"spread {spread_pct:.2f}% too wide"
        result["quality"] = "poor"
    elif volume < min_volume and oi < min_oi:
        result["blocked"] = True
        result["block_reason"] = "thin option liquidity"
        result["quality"] = "poor"
    elif spread_pct > caution_spread or imbalance < -0.6:
        result["quality"] = "caution"
        result["notes"].append("entry may slip; prefer limit order near ask")
    else:
        result["quality"] = "good"

    return result


def apply_execution_to_signal(signal: dict, execution: dict) -> dict:
    if not signal or signal.get("signal") != "ENTER":
        return signal

    updated = dict(signal)
    if execution.get("blocked"):
        updated["signal"] = "WAIT"
        updated["confidence"] = "low"
        updated["decision_reason"] = (
            f"{updated.get('decision_reason', '')} | EXECUTION_BLOCK - "
            f"{execution.get('block_reason', '')}"
        )
        return updated

    suggested = execution.get("suggested_entry")
    if suggested and suggested > 0:
        updated["entry_price"] = suggested
        risk = max(updated["entry_price"] - updated.get("stop_loss", 0), 1)
        updated["risk_points"] = round(risk, 2)
        updated["target_1"] = round(updated["entry_price"] + risk * 1.5, 2)
        updated["target_2"] = round(updated["entry_price"] + risk * 2.5, 2)
        updated["decision_reason"] = (
            f"{updated.get('decision_reason', '')} | EXECUTION_{execution.get('quality', 'unknown').upper()} "
            f"spread={execution.get('spread_pct')}%"
        )
    return updated
