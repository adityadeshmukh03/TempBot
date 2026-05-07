# position_tracker.py
#
# Single source of truth for:
#   1. Whether we currently hold an open position (prevents double-entry)
#   2. Cumulative realised PnL for today (daily loss circuit breaker)
#
# Designed as a module-level singleton — import and call anywhere.
# Thread-safe for the single-threaded analysis loop; KiteTicker runs
# callbacks on its own thread so all public mutators use a lock.
#
# ────────────────────────────────────────────────────────

import threading
from datetime import date, datetime
import config


# ─── INTERNAL STATE ────────────────────────────────────

_lock = threading.Lock()

_state = {
    # Open position fields (all None when flat)
    "in_position":   False,
    "entry_price":   None,
    "stop_loss":     None,
    "target_1":      None,
    "target_2":      None,
    "lots":          0,
    "units":         0,
    "entry_time":    None,
    "symbol":        None,

    # Daily PnL accumulator
    "daily_pnl":     0.0,
    "pnl_date":      date.today(),    # reset automatically on a new calendar day
    "trades_today":  0,
    "wins_today":    0,
    "losses_today":  0,

    # Circuit breaker latch — set True when daily loss limit is breached
    "circuit_open":  False,
}


# ─── HELPERS ───────────────────────────────────────────

def _ensure_fresh_day():
    """Reset daily counters if the calendar date has rolled over."""
    today = date.today()
    if _state["pnl_date"] != today:
        _state["daily_pnl"]    = 0.0
        _state["pnl_date"]     = today
        _state["trades_today"] = 0
        _state["wins_today"]   = 0
        _state["losses_today"] = 0
        _state["circuit_open"] = False
        print(f"[TRACKER] New trading day {today} — daily counters reset.")


# ─── POSITION ENTRY ────────────────────────────────────

def open_position(
    entry_price: float,
    stop_loss:   float,
    target_1:    float,
    target_2:    float,
    lots:        int,
    units:       int,
    symbol:      str = "",
) -> bool:
    """
    Record an entered position.

    Returns True if position was opened, False if already in a position
    (caller should treat False as a no-op — the signal is stale).
    """
    with _lock:
        _ensure_fresh_day()

        if _state["in_position"]:
            print(
                f"[TRACKER] open_position() called but already in position "
                f"({_state['symbol']} entered at {_state['entry_price']}). "
                f"Ignoring duplicate signal."
            )
            return False

        _state.update({
            "in_position": True,
            "entry_price": entry_price,
            "stop_loss":   stop_loss,
            "target_1":    target_1,
            "target_2":    target_2,
            "lots":        lots,
            "units":       units,
            "entry_time":  datetime.now(),
            "symbol":      symbol,
        })
        print(
            f"[TRACKER] Position OPENED — {symbol} | "
            f"Entry: {entry_price} | SL: {stop_loss} | "
            f"T1: {target_1} | T2: {target_2} | "
            f"Lots: {lots} ({units} units)"
        )
        return True


# ─── POSITION EXIT ─────────────────────────────────────

def close_position(
    exit_price:  float,
    exit_reason: str,            # "TARGET_1" / "TARGET_2" / "STOP_LOSS" / "MANUAL" / "EOD"
) -> dict:
    """
    Record a closed position and accumulate daily PnL.

    Returns a summary dict.  Calling this when flat is a no-op.
    """
    with _lock:
        _ensure_fresh_day()

        if not _state["in_position"]:
            print("[TRACKER] close_position() called but no open position. Ignoring.")
            return {}

        entry  = _state["entry_price"]
        units  = _state["units"]
        symbol = _state["symbol"]

        pnl_pts = round(exit_price - entry, 2)
        pnl_inr = round(pnl_pts * units, 2)

        _state["daily_pnl"]    += pnl_inr
        _state["trades_today"] += 1

        if pnl_inr > 0:
            outcome = "WIN"
            _state["wins_today"] += 1
        elif pnl_inr < 0:
            outcome = "LOSS"
            _state["losses_today"] += 1
        else:
            outcome = "BREAKEVEN"

        # Check daily loss circuit breaker
        limit = config.DAILY_LOSS_LIMIT
        if limit < 0 and _state["daily_pnl"] <= limit:
            _state["circuit_open"] = True
            print(
                f"[CIRCUIT BREAKER] Daily PnL ₹{_state['daily_pnl']:,.0f} "
                f"hit limit ₹{limit:,.0f}. No new entries for today."
            )

        summary = {
            "symbol":      symbol,
            "entry_price": entry,
            "exit_price":  exit_price,
            "exit_reason": exit_reason,
            "pnl_pts":     pnl_pts,
            "pnl_inr":     pnl_inr,
            "outcome":     outcome,
            "daily_pnl":   _state["daily_pnl"],
        }

        # Clear position fields
        _state.update({
            "in_position": False,
            "entry_price": None,
            "stop_loss":   None,
            "target_1":    None,
            "target_2":    None,
            "lots":        0,
            "units":       0,
            "entry_time":  None,
            "symbol":      None,
        })

        print(
            f"[TRACKER] Position CLOSED — {symbol} | "
            f"{exit_reason} @ {exit_price} | "
            f"PnL: {pnl_pts:+.2f} pts | ₹{pnl_inr:+,.0f} | "
            f"Day total: ₹{_state['daily_pnl']:+,.0f}"
        )
        return summary


def reduce_position(exit_price: float, exit_reason: str, units_to_exit: int) -> dict:
    """Partially close the current position and keep the remainder open."""
    with _lock:
        _ensure_fresh_day()
        if not _state["in_position"] or units_to_exit <= 0:
            return {}
        units_to_exit = min(int(units_to_exit), int(_state["units"]))
        entry = _state["entry_price"]
        pnl_pts = round(exit_price - entry, 2)
        pnl_inr = round(pnl_pts * units_to_exit, 2)
        _state["daily_pnl"] += pnl_inr
        _state["units"] -= units_to_exit
        if config.LOT_SIZE:
            _state["lots"] = max(0, int(_state["units"] / config.LOT_SIZE))
        summary = {
            "symbol": _state["symbol"],
            "entry_price": entry,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pts": pnl_pts,
            "pnl_inr": pnl_inr,
            "units_closed": units_to_exit,
            "units_remaining": _state["units"],
            "daily_pnl": _state["daily_pnl"],
        }
        if _state["units"] <= 0:
            _state["in_position"] = False
        print(
            f"[TRACKER] Position PARTIAL EXIT - {exit_reason} @ {exit_price} | "
            f"Closed: {units_to_exit} | Remaining: {_state['units']} | "
            f"PnL: ₹{pnl_inr:+,.0f}"
        )
        return summary


# ─── READ-ONLY QUERIES ─────────────────────────────────

def is_in_position() -> bool:
    """True if a position is currently open."""
    with _lock:
        return _state["in_position"]


def is_circuit_open() -> bool:
    """
    True if the daily loss limit has been breached.
    When True, run_analysis() should skip all entry logic.
    """
    with _lock:
        _ensure_fresh_day()
        return _state["circuit_open"]


def get_daily_pnl() -> float:
    """Cumulative realised PnL for today in ₹."""
    with _lock:
        _ensure_fresh_day()
        return _state["daily_pnl"]


def get_position_snapshot() -> dict:
    """Return a copy of the current position state (safe to log/print)."""
    with _lock:
        return {k: v for k, v in _state.items()}


def get_daily_risk_snapshot() -> dict:
    """Return only the day-level risk counters that must survive restarts."""
    with _lock:
        _ensure_fresh_day()
        return {
            "daily_pnl": _state["daily_pnl"],
            "pnl_date": _state["pnl_date"],
            "trades_today": _state["trades_today"],
            "wins_today": _state["wins_today"],
            "losses_today": _state["losses_today"],
            "circuit_open": _state["circuit_open"],
        }


def update_stop_loss(stop_loss: float) -> bool:
    """Update stop loss for the current open position."""
    with _lock:
        if not _state["in_position"]:
            return False
        _state["stop_loss"] = float(stop_loss)
        return True


def restore_position(snapshot: dict) -> bool:
    """Restore open position state after a process restart."""
    if not snapshot or not snapshot.get("in_position"):
        return False
    with _lock:
        restored = dict(snapshot)
        if isinstance(restored.get("entry_time"), str):
            try:
                restored["entry_time"] = datetime.fromisoformat(restored["entry_time"])
            except ValueError:
                restored["entry_time"] = None
        if isinstance(restored.get("pnl_date"), str):
            try:
                restored["pnl_date"] = date.fromisoformat(restored["pnl_date"])
            except ValueError:
                restored["pnl_date"] = date.today()
        _state.update(restored)
        print(f"[TRACKER] Restored open position: {_state.get('symbol')} @ {_state.get('entry_price')}")
        return True


def restore_daily_risk(snapshot: dict) -> bool:
    """Restore daily PnL/circuit state without touching the open position."""
    if not snapshot:
        return False
    with _lock:
        restored = dict(snapshot)
        if isinstance(restored.get("pnl_date"), str):
            try:
                restored["pnl_date"] = date.fromisoformat(restored["pnl_date"])
            except ValueError:
                restored["pnl_date"] = date.today()
        if restored.get("pnl_date") != date.today():
            return False
        for key in ("daily_pnl", "pnl_date", "trades_today", "wins_today", "losses_today", "circuit_open"):
            if key in restored:
                _state[key] = restored[key]
        print(
            f"[TRACKER] Restored daily risk state: "
            f"Day PnL: ₹{_state['daily_pnl']:+,.0f} | "
            f"Trades: {_state['trades_today']} | "
            f"Circuit: {'OPEN' if _state['circuit_open'] else 'OK'}"
        )
        return True


def get_daily_summary() -> str:
    """One-line string for terminal logging."""
    with _lock:
        _ensure_fresh_day()
        return (
            f"Day PnL: ₹{_state['daily_pnl']:+,.0f} | "
            f"Trades: {_state['trades_today']} | "
            f"W/L: {_state['wins_today']}/{_state['losses_today']} | "
            f"Circuit: {'OPEN' if _state['circuit_open'] else 'OK'}"
        )


# ─── MANUAL DAILY PnL ADJUSTMENT ──────────────────────
# Call this if you enter/exit manually outside the bot
# (e.g. you placed a trade by hand and want it counted).

def adjust_daily_pnl(amount_inr: float, reason: str = "manual"):
    """Manually add/subtract ₹ from today's PnL accumulator."""
    with _lock:
        _ensure_fresh_day()
        _state["daily_pnl"] += amount_inr
        limit = config.DAILY_LOSS_LIMIT
        if limit < 0 and _state["daily_pnl"] <= limit:
            _state["circuit_open"] = True
            print(
                f"[CIRCUIT BREAKER] Manual adjustment triggered limit. "
                f"Day PnL: ₹{_state['daily_pnl']:,.0f}"
            )
        print(
            f"[TRACKER] Manual PnL adjustment: ₹{amount_inr:+,.0f} ({reason}) | "
            f"Day total: ₹{_state['daily_pnl']:+,.0f}"
        )
