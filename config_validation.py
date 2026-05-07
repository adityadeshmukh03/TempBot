from __future__ import annotations

from datetime import datetime


def validate_config(config) -> list[str]:
    errors = []

    if getattr(config, "INSTRUMENT", "").upper() not in getattr(config, "INSTRUMENT_EXCHANGE", {}):
        errors.append("INSTRUMENT must exist in INSTRUMENT_EXCHANGE")
    if not (0 < float(getattr(config, "RISK_PER_TRADE_PCT", 0)) <= 5):
        errors.append("RISK_PER_TRADE_PCT must be > 0 and <= 5")
    if int(getattr(config, "LOT_SIZE", 0)) <= 0:
        errors.append("LOT_SIZE must be positive")
    if int(getattr(config, "MAX_LOTS", 0)) <= 0:
        errors.append("MAX_LOTS must be positive")
    if getattr(config, "ENTRY_ORDER_TYPE", "MARKET") not in ("MARKET", "LIMIT"):
        errors.append("ENTRY_ORDER_TYPE must be MARKET or LIMIT")
    if getattr(config, "EXIT_ORDER_TYPE", "MARKET") not in ("MARKET", "LIMIT"):
        errors.append("EXIT_ORDER_TYPE must be MARKET or LIMIT")

    # Paper mode is gone — SL and EOD exit are always required.
    if not getattr(config, "ENABLE_SL_ORDER", False):
        errors.append("ENABLE_SL_ORDER must be True — broker SL-M is mandatory")
    if not getattr(config, "AUTO_EOD_EXIT", False):
        errors.append("AUTO_EOD_EXIT must be True — EOD square-off is mandatory")

    if float(getattr(config, "ORDER_FILL_TIMEOUT_SECONDS", 0)) <= 0:
        errors.append("ORDER_FILL_TIMEOUT_SECONDS must be positive")
    if float(getattr(config, "ORDER_FILL_POLL_SECONDS", 0)) <= 0:
        errors.append("ORDER_FILL_POLL_SECONDS must be positive")
    if not (0.1 <= float(getattr(config, "TARGET_DELTA", 0)) <= 0.9):
        errors.append("TARGET_DELTA must be between 0.10 and 0.90")
    if not (0 <= float(getattr(config, "DELTA_TOLERANCE", -1)) <= 0.5):
        errors.append("DELTA_TOLERANCE must be between 0 and 0.50")
    try:
        datetime.strptime(getattr(config, "FILTER_EXPIRY_CUTOFF", ""), "%H:%M")
    except ValueError:
        errors.append("FILTER_EXPIRY_CUTOFF must be HH:MM")
    try:
        datetime.strptime(getattr(config, "EOD_EXIT_TIME", ""), "%H:%M")
    except ValueError:
        errors.append("EOD_EXIT_TIME must be HH:MM")

    required_keys = ("KITE_API_KEY", "KITE_API_SECRET", "GEMINI_API_KEY")
    for key in required_keys:
        value = str(getattr(config, key, "") or "").strip()
        if not value:
            errors.append(f"{key} is required")
        elif value.lower().startswith("your_") or "placeholder" in value.lower():
            errors.append(f"{key} still contains a placeholder value")

    if errors:
        raise ValueError("Config validation failed: " + "; ".join(errors))
    return []
