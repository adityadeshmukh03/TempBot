from __future__ import annotations

import csv
import os
from collections import defaultdict
from typing import Optional

import config


def _read_rows(file: str) -> list[dict]:
    if not os.path.exists(file):
        return []
    with open(file, "r", newline="") as f:
        return list(csv.DictReader(f))


def _completed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("outcome") in ("WIN", "LOSS", "BREAKEVEN")]


def _win_rate(rows: list[dict]) -> float:
    completed = _completed(rows)
    if not completed:
        return 0.0
    wins = [r for r in completed if r.get("outcome") == "WIN"]
    return len(wins) / len(completed)


def get_runtime_adjustments(file: Optional[str] = None) -> dict:
    """
    Conservative adaptive layer.

    It only tightens gates after enough completed trades exist. It never lowers
    the confluence bar from historical data, which keeps early overfitting from
    turning into live risk.
    """
    file = file or config.PERF_LOG_FILE
    rows = _read_rows(file)
    completed = _completed(rows)
    adjustments = {
        "available": bool(completed),
        "sample_size": len(completed),
        "global_required_add": 0,
        "regime_required_add": {},
        "weak_flags": [],
        "notes": [],
    }

    min_completed = getattr(config, "SELF_OPT_MIN_COMPLETED_TRADES", 20)
    min_segment = getattr(config, "SELF_OPT_MIN_SEGMENT_TRADES", 6)

    if len(completed) < min_completed:
        adjustments["notes"].append(
            f"adaptive optimizer warming up ({len(completed)}/{min_completed} completed trades)"
        )
        return adjustments

    global_wr = _win_rate(completed)
    if global_wr < 0.42:
        adjustments["global_required_add"] = 1
        adjustments["notes"].append(f"global win rate {global_wr:.1%}; raising confluence by 1")

    by_regime = defaultdict(list)
    by_flag = defaultdict(list)
    for row in completed:
        if row.get("regime"):
            by_regime[row["regime"]].append(row)
        for flag in (row.get("audit_flags") or "").split("|"):
            if flag:
                by_flag[flag].append(row)

    for regime, subset in by_regime.items():
        if len(subset) >= min_segment and _win_rate(subset) < 0.38:
            adjustments["regime_required_add"][regime] = 1
            adjustments["notes"].append(f"{regime} underperforming; raising bar")

    for flag, subset in by_flag.items():
        if len(subset) >= min_segment and _win_rate(subset) < 0.35:
            adjustments["weak_flags"].append(flag)

    return adjustments


def apply_runtime_adjustments(confluence: dict, regime: dict, strategy_edge: dict,
                              adjustments: dict) -> dict:
    updated = dict(confluence)
    add = int(regime.get("confluence_adjustment", 0) or 0)
    add += int(adjustments.get("global_required_add", 0) or 0)
    add += int(adjustments.get("regime_required_add", {}).get(regime.get("regime"), 0) or 0)

    if strategy_edge.get("warnings") and not strategy_edge.get("supported"):
        add += 1

    updated["base_required"] = confluence.get("required")
    updated["required"] = max(1, int(confluence.get("required", 1)) + add)
    updated["met"] = updated.get("score", 0) >= updated["required"] and not updated.get("oi_block")
    updated["adaptive_adjustment"] = add
    updated["adaptive_notes"] = adjustments.get("notes", [])
    return updated
