# performance_tracker.py
#
# Tracks every signal Gemini produces and lets you compare:
#   - VETO  trades: what would have happened if we ignored the veto?
#   - WARN  trades: are WARN trades worse than PASS trades?
#   - PASS  trades: baseline performance
#
# Two log files:
#   gemini_performance.csv  — one row per signal (written at signal time)
#   (trade_log.csv)         — already exists in main.py (outcome written at exit)
#
# After market: run analyse_performance() to print a full scorecard.
# ────────────────────────────────────────────────────────

import csv
import os
from datetime import datetime, date
from collections import defaultdict
import config


# ─── SIGNAL LOGGING ────────────────────────────────────

PERF_HEADERS = [
    "date", "time", "symbol", "direction", "condition", "signal",
    "audit_verdict", "confidence", "confluence_score", "confluence_required",
    "entry_price", "stop_loss", "target_1", "target_2",
    "risk_points", "lots", "max_loss_inr",
    "iv_rank", "dte", "adx", "pcr_oi", "buildup_bias",
    "regime", "regime_confidence", "market_alignment",
    "liquidity_bias", "liquidity_events", "execution_quality",
    "spread_pct", "adaptive_adjustment", "strategy_edge",
    "veto_reason", "warn_reasons", "audit_flags",
    # outcome columns — filled later by log_outcome()
    "exit_price", "exit_time", "exit_reason",
    "pnl_points", "pnl_inr", "outcome",
]


def _ensure_perf_headers(file: str):
    if not os.path.exists(file):
        return
    with open(file, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        existing = reader.fieldnames or []
    if existing == PERF_HEADERS:
        return
    with open(file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PERF_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in PERF_HEADERS})


def log_signal(
    signal:      dict,
    sizing:      dict,
    option_data: dict = None,
    oi_result:   dict = None,
    indicators:  dict = None,
    symbol:      str  = "",
    direction:   str  = "CE",
    file:        str  = None,
):
    """
    Write one row to gemini_performance.csv at signal time.
    Outcome columns are left blank — filled by log_outcome() later.
    """
    file = file or config.PERF_LOG_FILE
    _ensure_perf_headers(file)
    exists = os.path.exists(file)

    od  = option_data or {}
    ind = indicators  or {}
    pcr = (oi_result or {}).get('pcr', {})
    oib = (oi_result or {}).get('oi_buildup', {})

    now = datetime.now()

    row = {
        "date":                now.strftime("%Y-%m-%d"),
        "time":                now.strftime("%H:%M"),
        "symbol":              symbol,
        "direction":           direction,
        "condition":           signal.get("condition", ""),
        "signal":              signal.get("signal", ""),
        "audit_verdict":       signal.get("audit_verdict", ""),
        "confidence":          signal.get("confidence", ""),
        "confluence_score":    signal.get("confluence_score", ""),
        "confluence_required": signal.get("confluence_required", ""),  # set below
        "entry_price":         signal.get("entry_price", ""),
        "stop_loss":           signal.get("stop_loss", ""),
        "target_1":            signal.get("target_1", ""),
        "target_2":            signal.get("target_2", ""),
        "risk_points":         signal.get("risk_points", ""),
        "lots":                sizing.get("lots", ""),
        "max_loss_inr":        sizing.get("max_loss", ""),
        "iv_rank":             od.get("iv_rank", ""),
        "dte":                 od.get("days_to_expiry", ""),
        "adx":                 ind.get("adx", ""),
        "pcr_oi":              pcr.get("pcr_oi", ""),
        "buildup_bias":        oib.get("buildup_bias", ""),
        "regime":              signal.get("regime", ""),
        "regime_confidence":   signal.get("regime_confidence", ""),
        "market_alignment":    signal.get("market_alignment", ""),
        "liquidity_bias":      signal.get("liquidity_bias", ""),
        "liquidity_events":    "|".join(signal.get("liquidity_events", [])),
        "execution_quality":   signal.get("execution_quality", ""),
        "spread_pct":          signal.get("spread_pct", ""),
        "adaptive_adjustment": signal.get("adaptive_adjustment", ""),
        "strategy_edge":       signal.get("strategy_edge", ""),
        "veto_reason":         signal.get("veto_reason", ""),
        "warn_reasons":        signal.get("warn_reasons", ""),
        "audit_flags":         "|".join(signal.get("audit_flags", [])),
        # outcome — blank at signal time
        "exit_price":          "",
        "exit_time":           "",
        "exit_reason":         "",
        "pnl_points":          "",
        "pnl_inr":             "",
        "outcome":             "",
    }

    with open(file, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PERF_HEADERS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"[PERF] Logged {signal.get('signal')} ({signal.get('audit_verdict')}) to {file}")


def log_outcome(
    entry_time:   str,
    exit_price:   float,
    exit_reason:  str,         # "TARGET_1" / "TARGET_2" / "STOP_LOSS" / "MANUAL" / "EOD"
    lots:         int,
    lot_size:     int   = None,
    entry_price:  float = None,  # optional — used as secondary match key to avoid
    file:         str   = None,  #            ambiguity when two signals fire in the same minute
):
    """
    Find the most recent unresolved ENTER row matching entry_time and fill outcome.
    Call this when a trade exits (manual or automated).

    exit_reason choices: TARGET_1, TARGET_2, STOP_LOSS, MANUAL, EOD

    If two ENTER signals fired in the same minute (rare but possible), pass
    entry_price to disambiguate — only rows whose entry_price matches within
    ±0.01 pts will be considered.
    """
    file     = file or config.PERF_LOG_FILE
    lot_size = lot_size or config.LOT_SIZE

    if not os.path.exists(file):
        print(f"[PERF] No performance log found at {file}")
        return

    rows = []
    updated = False

    with open(file, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows   = list(reader)

    # Find the matching ENTER row with no outcome yet (search from end).
    # Primary key : time == entry_time
    # Secondary key: entry_price match (within ±0.01) when supplied — disambiguates
    #                two signals that fired in the same minute.
    for row in reversed(rows):
        time_match  = row.get("time") == entry_time
        signal_ok   = row.get("signal") == "ENTER"
        outcome_ok  = row.get("outcome") == ""

        if entry_price is not None:
            try:
                row_entry = float(row.get("entry_price", 0))
                price_match = abs(row_entry - entry_price) <= 0.01
            except (ValueError, TypeError):
                price_match = False
        else:
            price_match = True   # not supplied — skip secondary check

        if (signal_ok and outcome_ok and time_match and price_match):

            entry  = float(row["entry_price"])
            pnl_pts = round(exit_price - entry, 2)
            units   = lots * lot_size
            pnl_inr = round(pnl_pts * units, 2)

            if pnl_pts > 0:
                outcome = "WIN"
            elif pnl_pts < 0:
                outcome = "LOSS"
            else:
                outcome = "BREAKEVEN"

            row["exit_price"]  = exit_price
            row["exit_time"]   = datetime.now().strftime("%H:%M")
            row["exit_reason"] = exit_reason
            row["pnl_points"]  = pnl_pts
            row["pnl_inr"]     = pnl_inr
            row["outcome"]     = outcome
            updated = True
            break

    if not updated:
        print(f"[PERF] No matching ENTER row found for time={entry_time}")
        return

    with open(file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PERF_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[PERF] Outcome logged — {exit_reason} | PnL: {pnl_pts} pts | ₹{pnl_inr:,.0f}")


# ─── PERFORMANCE ANALYSIS ──────────────────────────────

def analyse_performance(file: str = None, since_date: str = None) -> dict:
    """
    Read the performance log and compute a full scorecard.

    Parameters
    ----------
    file       : path to gemini_performance.csv
    since_date : "YYYY-MM-DD" — only include rows from this date onward

    Returns
    -------
    dict with full stats, also prints a formatted report to terminal.
    """
    file = file or config.PERF_LOG_FILE

    if not os.path.exists(file):
        print(f"[PERF] No log at {file} — no trades recorded yet")
        return {}

    with open(file, "r", newline="") as f:
        rows = [r for r in csv.DictReader(f)]

    if since_date:
        rows = [r for r in rows if r.get("date", "") >= since_date]

    if not rows:
        print("[PERF] No rows found for the specified date range")
        return {}

    # ── Segment by verdict ───────────────────────────
    by_verdict  = defaultdict(list)
    by_signal   = defaultdict(list)
    by_outcome  = defaultdict(list)
    by_direction = defaultdict(list)

    for r in rows:
        verdict = r.get("audit_verdict", "UNKNOWN")
        signal  = r.get("signal", "UNKNOWN")
        direction = r.get("direction", "UNKNOWN")
        outcome = r.get("outcome", "")
        by_verdict[verdict].append(r)
        by_signal[signal].append(r)
        by_direction[direction].append(r)
        if outcome:
            by_outcome[outcome].append(r)

    def _stats(subset):
        completed  = [r for r in subset if r.get("outcome") in ("WIN", "LOSS", "BREAKEVEN")]
        wins       = [r for r in completed if r.get("outcome") == "WIN"]
        losses     = [r for r in completed if r.get("outcome") == "LOSS"]
        pnl_list   = []
        for r in completed:
            try:
                pnl_list.append(float(r.get("pnl_inr", 0) or 0))
            except ValueError:
                pass
        total_pnl  = sum(pnl_list)
        win_rate   = len(wins) / len(completed) * 100 if completed else 0
        avg_win    = sum(float(r.get("pnl_inr", 0) or 0) for r in wins) / len(wins) if wins else 0
        avg_loss   = sum(float(r.get("pnl_inr", 0) or 0) for r in losses) / len(losses) if losses else 0
        rr         = abs(avg_win / avg_loss) if avg_loss != 0 else None
        return {
            "total":     len(subset),
            "completed": len(completed),
            "wins":      len(wins),
            "losses":    len(losses),
            "win_rate":  round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_win":   round(avg_win, 2),
            "avg_loss":  round(avg_loss, 2),
            "rr_ratio":  round(rr, 2) if rr else "N/A",
        }

    # ── VETO opportunity cost ─────────────────────────
    # VETO rows have signal=WAIT. We estimate what would have happened
    # if we had entered at the entry_price they computed.
    veto_rows = [r for r in by_verdict.get("VETO", []) if r.get("entry_price")]

    stats = {
        "ALL":           _stats(rows),
        "PASS_trades":   _stats(by_verdict.get("PASS", [])),
        "WARN_trades":   _stats(by_verdict.get("WARN", [])),
        "VETO_blocked":  _stats(veto_rows),
        "ENTER_signals": _stats(by_signal.get("ENTER", [])),
        "WAIT_signals":  _stats(by_signal.get("WAIT", [])),
        "CE_trades":     _stats(by_direction.get("CE", [])),
        "PE_trades":     _stats(by_direction.get("PE", [])),
    }

    # ── Print report ──────────────────────────────────
    _print_report(stats, rows)
    return stats


def _print_report(stats: dict, rows: list):
    sep = "=" * 60
    print(f"\n{sep}")
    print("  GEMINI PERFORMANCE SCORECARD")
    print(sep)

    total_rows = len(rows)
    completed  = len([r for r in rows if r.get("outcome") in ("WIN", "LOSS", "BREAKEVEN")])
    print(f"  Total signals logged  : {total_rows}")
    print(f"  Completed (with exit) : {completed}")
    print(f"  Pending (no exit)     : {total_rows - completed}")
    print()

    for label, s in stats.items():
        if s["total"] == 0:
            continue
        rr = f"  RR: {s['rr_ratio']}" if s.get('rr_ratio') != 'N/A' else ""
        print(f"  [{label}]")
        print(f"    Signals : {s['total']}  |  Completed: {s['completed']}")
        print(f"    Win/Loss: {s['wins']}W / {s['losses']}L  |  Win Rate: {s['win_rate']}%{rr}")
        print(f"    Avg Win : ₹{s['avg_win']:,.0f}  |  Avg Loss: ₹{s['avg_loss']:,.0f}")
        print(f"    Total PnL: ₹{s['total_pnl']:,.0f}")
        print()

    # ── VETO value assessment ─────────────────────────
    veto_stats  = stats.get("VETO_blocked", {})
    enter_stats = stats.get("ENTER_signals", {})
    if veto_stats.get("total", 0) > 0:
        print("  [VETO ANALYSIS — Would these have worked?]")
        print(f"    Vetoed trades  : {veto_stats['total']}")
        print(f"    Veto Win Rate  : {veto_stats['win_rate']}%  (lower = Gemini was RIGHT to veto)")
        if enter_stats.get("win_rate") and veto_stats.get("win_rate") is not None:
            delta = enter_stats["win_rate"] - veto_stats["win_rate"]
            verdict = "Gemini vetoes ADDING VALUE" if delta > 5 else \
                      "Gemini vetoes NEUTRAL"       if delta > -5 else \
                      "Gemini vetoes COSTING YOU"
            print(f"    ENTER win rate : {enter_stats['win_rate']}%")
            print(f"    Delta          : {delta:+.1f}%  → {verdict}")
        print()

    print(sep + "\n")


# ─── DAILY SUMMARY ─────────────────────────────────────

def daily_summary(file: str = None):
    """Quick end-of-day PnL summary. Call after market close."""
    file  = file or config.PERF_LOG_FILE
    today = date.today().strftime("%Y-%m-%d")
    analyse_performance(file=file, since_date=today)
