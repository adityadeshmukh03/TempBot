from __future__ import annotations

import csv
import json
import os
import signal
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = ROOT
PID_FILE = RUNTIME_DIR / "bot.pid"
STATIC_DIR = ROOT

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402


app = FastAPI(title="AI Trading Bot GUI API")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class StartRequest(BaseModel):
    live_mode: bool = False
    confirm: str = ""
    wait_for_market_open: bool = True


def _state_db() -> Path:
    return ROOT / getattr(config, "STATE_DB_PATH", "state/bot_state.sqlite")


def _log_dir() -> Path:
    return ROOT / getattr(config, "LOG_DIR", "logs")


def _read_state(key: str, default: Any = None) -> Any:
    db = _state_db()
    if not db.exists():
        return default
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return default


def _tail(path: Path, lines: int = 120) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(errors="replace").splitlines()
    return text[-max(1, min(lines, 500)) :]


def _csv_tail(path: Path, rows: int = 20) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", errors="replace") as handle:
        data = list(csv.DictReader(handle))
    return data[-max(1, min(rows, 100)) :]


def _pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        return None


def _process_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        running = str(pid) in result.stdout
        if not running:
            PID_FILE.unlink(missing_ok=True)
        return running
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        PID_FILE.unlink(missing_ok=True)
        return False


def _bot_process() -> dict[str, Any]:
    pid = _pid()
    running = _process_running(pid)
    return {"running": running, "pid": pid if running else None}


def _public_config() -> dict[str, Any]:
    return {
        "instrument": getattr(config, "INSTRUMENT", ""),
        "candle_interval": getattr(config, "CANDLE_INTERVAL", ""),
        "live_mode_default": bool(getattr(config, "LIVE_MODE", False)),
        "broker_sl_enabled": bool(getattr(config, "ENABLE_SL_ORDER", False)),
        "auto_eod_exit": bool(getattr(config, "AUTO_EOD_EXIT", False)),
        "eod_exit_time": getattr(config, "EOD_EXIT_TIME", ""),
        "total_capital": getattr(config, "TOTAL_CAPITAL", ""),
        "risk_per_trade_pct": getattr(config, "RISK_PER_TRADE_PCT", ""),
        "lot_size": getattr(config, "LOT_SIZE", ""),
        "max_lots": getattr(config, "MAX_LOTS", ""),
        "state_db_exists": _state_db().exists(),
        "access_token_exists": (ROOT / "access_token.txt").exists(),
        "kite_key_set": not str(getattr(config, "KITE_API_KEY", "")).lower().startswith("your_"),
        "gemini_key_set": not str(getattr(config, "GEMINI_API_KEY", "")).lower().startswith("your_"),
    }


def _model_flow() -> list[dict[str, str]]:
    return [
        {"step": "1", "name": "Listen", "detail": "Read live option and underlying ticks from Kite."},
        {"step": "2", "name": "Build Candles", "detail": "Turn ticks into clean 5-minute candles."},
        {"step": "3", "name": "Cheap Filters", "detail": "Skip bad markets before spending API calls."},
        {"step": "4", "name": "Market Context", "detail": "Check spot, higher timeframe trend, OI, regime, and liquidity."},
        {"step": "5", "name": "Pick Direction", "detail": "Choose CE, PE, or no trade."},
        {"step": "6", "name": "Gemini Audit", "detail": "Let Gemini look for reasons to block the trade."},
        {"step": "7", "name": "Size Trade", "detail": "Calculate lots from capital, risk, entry, and stop loss."},
        {"step": "8", "name": "Execute", "detail": "Place paper or live orders and register broker SL when enabled."},
        {"step": "9", "name": "Manage Exit", "detail": "Watch stop, targets, trailing stop, broker sync, and EOD square-off."},
        {"step": "10", "name": "Save State", "detail": "Persist position, daily risk, logs, signals, and OI baseline."},
    ]


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "time": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
    }


@app.get("/api/config")
def public_config():
    return _public_config()


@app.get("/api/model-flow")
def model_flow():
    return {"flow": _model_flow()}


@app.get("/api/bot/process")
def bot_process():
    return _bot_process()


@app.get("/api/status")
def status():
    trade_rows = _csv_tail(ROOT / getattr(config, "TRADE_LOG_FILE", "trade_log.csv"), rows=10)
    perf_rows = _csv_tail(ROOT / getattr(config, "PERF_LOG_FILE", "gemini_performance.csv"), rows=10)
    runtime_tail = _tail(_log_dir() / "runtime.log", lines=20)
    error_tail = _tail(_log_dir() / "errors.log", lines=20)
    return {
        "process": _bot_process(),
        "config": _public_config(),
        "model_flow": _model_flow(),
        "open_position": _read_state("open_position", {}),
        "daily_risk": _read_state("daily_risk", {}),
        "runtime_stats": _read_state("runtime_stats", {}),
        "session_state": _read_state("session_state", {}),
        "recent_trades": trade_rows,
        "recent_signals": perf_rows,
        "runtime_log": runtime_tail,
        "error_log": error_tail,
    }


@app.get("/api/logs")
def logs(name: str = "runtime", lines: int = 120):
    allowed = {
        "runtime": _log_dir() / "runtime.log",
        "errors": _log_dir() / "errors.log",
        "trades": _log_dir() / "trades.log",
        "latency": _log_dir() / "latency.log",
    }
    if name not in allowed:
        raise HTTPException(status_code=400, detail="Unknown log name")
    return {"name": name, "lines": _tail(allowed[name], lines)}


@app.post("/api/bot/start")
def start_bot(request: StartRequest):
    current = _bot_process()
    if current["running"]:
        return {"started": False, "message": "Bot is already running", "process": current}
    if request.live_mode and request.confirm.strip() != "LIVE":
        raise HTTPException(status_code=400, detail="Type LIVE to start real-money mode")

    env = os.environ.copy()
    env["LIVE_MODE"] = "true" if request.live_mode else "false"
    env["WAIT_FOR_MARKET_OPEN"] = "true" if request.wait_for_market_open else "false"

    log_file = RUNTIME_DIR / "bot_process.log"
    handle = log_file.open("a", buffering=1)
    handle.write(f"\n--- starting bot {datetime.now().isoformat(timespec='seconds')} ---\n")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "main.py")],
        cwd=ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    PID_FILE.write_text(str(process.pid))
    return {"started": True, "message": "Bot started", "process": {"running": True, "pid": process.pid}}


@app.post("/api/bot/stop")
def stop_bot():
    pid = _pid()
    if not _process_running(pid):
        return {"stopped": False, "message": "Bot is not running", "process": {"running": False, "pid": None}}
    try:
        if os.name == "nt":
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not stop bot: {exc}") from exc
    PID_FILE.unlink(missing_ok=True)
    return {"stopped": True, "message": "Stop signal sent", "process": {"running": False, "pid": None}}
