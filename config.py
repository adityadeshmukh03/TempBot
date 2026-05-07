# config.py

import os


def _env_str(name, default=""):
    return os.getenv(name, default)


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def _env_float(name, default):
    value = os.getenv(name)
    return float(value) if value not in (None, "") else default

# ─── KITE / GEMINI CREDENTIALS ─────────────────────────
KITE_API_KEY    = _env_str("KITE_API_KEY", "your_kite_api_key")
KITE_API_SECRET = _env_str("KITE_API_SECRET", "your_kite_secret")
GEMINI_API_KEY  = _env_str("GEMINI_API_KEY", "your_gemini_api_key")

# ─── INSTRUMENT ────────────────────────────────────────
INSTRUMENT    = _env_str("INSTRUMENT", "NIFTY")
CANDLE_INTERVAL = _env_int("CANDLE_INTERVAL", 5)           # minutes

# --- STRIKE SELECTION -------------------------------------------------------
TARGET_DELTA        = 0.45   # target delta for strike selection (0.40-0.50 recommended)
DELTA_TOLERANCE     = 0.10   # accept strikes where |delta - TARGET_DELTA| <= this
STRIKE_STEP         = None   # None = auto-detect per instrument (recommended)
                             # or set manually: NIFTY=50, BANKNIFTY=100, SENSEX=100, FINNIFTY=50

# --- DIRECTION --------------------------------------------------------------
DIRECTION_MIXED_THRESHOLD  = 0       # alignment score must exceed this to take a trade
                                     # 0 = take any non-zero alignment
                                     # 1 = require at least 1 confirming HTF signal
DIRECTION_STRONG_THRESHOLD = 2       # alignment score >= this = high confidence direction

# --- EXCHANGE MAPPING -------------------------------------------------------
# BSE instruments (SENSEX) trade on BFO, all others on NFO
INSTRUMENT_EXCHANGE = {
    "NIFTY":      "NFO",
    "BANKNIFTY":  "NFO",
    "FINNIFTY":   "NFO",
    "SENSEX":     "BFO",
    "BANKEX":     "BFO",
}
UNDERLYING_EXCHANGE = {
    "NIFTY":      "NSE",
    "BANKNIFTY":  "NSE",
    "FINNIFTY":   "NSE",
    "SENSEX":     "BSE",
    "BANKEX":     "BSE",
}
UNDERLYING_SYMBOL = {
    "NIFTY":      "NIFTY 50",
    "BANKNIFTY":  "NIFTY BANK",
    "FINNIFTY":   "NIFTY FIN SERVICE",
    "SENSEX":     "SENSEX",
    "BANKEX":     "BANKEX",
}
STRIKE_STEP_MAP = {
    "NIFTY":      50,
    "BANKNIFTY":  100,
    "FINNIFTY":   50,
    "SENSEX":     100,
    "BANKEX":     100,
}

# ─── CAPITAL & POSITION SIZING ─────────────────────────
TOTAL_CAPITAL       = _env_float("TOTAL_CAPITAL", 500000)  # your total trading capital
RISK_PER_TRADE_PCT  = _env_float("RISK_PER_TRADE_PCT", 1.0)     # max % of capital to risk per trade
LOT_SIZE            = _env_int("LOT_SIZE", 50)      # NIFTY lot size (update for BANKNIFTY=15, etc.)
MAX_LOTS            = _env_int("MAX_LOTS", 5)       # hard cap - never trade more than this many lots

# ─── HARD FILTERS — NO TRADE CONDITIONS ────────────────
FILTER_IV_RANK_MAX     = 80   # block if IV Rank >= this (options too expensive)
FILTER_ADX_MIN         = 15   # block if ADX < this (dead/choppy market)
FILTER_EXPIRY_CUTOFF   = "14:00"  # block on expiry day after this time (HH:MM)

# ─── OI CONFLUENCE WEIGHTS ─────────────────────────────
# These control how much each OI signal contributes to confluence.
# Scores are added on top of the existing indicator confluence.
OI_CONFLUENCE_MAX_SCORE = 3   # max OI can add to confluence score

# Context/regime/execution intelligence.
EXECUTION_MAX_SPREAD_PCT      = 2.0
EXECUTION_CAUTION_SPREAD_PCT  = 1.0
EXECUTION_MIN_VOLUME          = 1000
EXECUTION_MIN_OI              = 10000
SELF_OPT_MIN_COMPLETED_TRADES = 20
SELF_OPT_MIN_SEGMENT_TRADES   = 6
GEMINI_FLASH_TIMEOUT_SECONDS  = 5
GEMINI_PRO_TIMEOUT_SECONDS    = 8
AUTO_EXIT_TARGET              = "TARGET_1"  # TARGET_1 or TARGET_2

# --- EXECUTION --------------------------------------------------------------
LIVE_MODE         = _env_bool("LIVE_MODE", False)     # False = paper/dry-run; True = place real broker orders
ENTRY_ORDER_TYPE  = "MARKET"  # MARKET or LIMIT
EXIT_ORDER_TYPE   = "MARKET"  # MARKET or LIMIT
ORDER_PRODUCT     = "MIS"
ENABLE_SL_ORDER   = _env_bool("ENABLE_SL_ORDER", True)     # place broker SL-M after entry when LIVE_MODE=True
PARTIAL_EXITS_ENABLED = False # True = exit PARTIAL_EXIT_PCT at T1 and rest at T2
PARTIAL_EXIT_PCT  = 0.50
TRAILING_SL_ENABLED = True
TRAIL_AFTER_R       = 1.0
TRAIL_BY_R          = 0.5
ORDER_FILL_TIMEOUT_SECONDS = 10
ORDER_FILL_POLL_SECONDS    = 0.5

# --- PERSISTENCE / INFRA ----------------------------------------------------
STATE_DB_PATH             = "bot_state.sqlite"
LOG_DIR                   = "."
OI_BASELINE_PATH          = "oi_baseline.json"
WS_STALE_AFTER_SECONDS    = 15
BROKER_RECONCILE_SECONDS  = 30
AUTO_EOD_EXIT             = _env_bool("AUTO_EOD_EXIT", True)
EOD_EXIT_TIME             = _env_str("EOD_EXIT_TIME", "15:20")
WAIT_FOR_MARKET_OPEN      = _env_bool("WAIT_FOR_MARKET_OPEN", True)
MAX_CONSECUTIVE_LOSSES    = 3
MAX_LATENCY_MS            = 2500
MAX_SLIPPAGE_PCT          = 2.0
API_FAILURE_THRESHOLD     = 5

# ─── OPTION CONFIGURATION ──────────────────────────────
# EXPIRY_TYPE: "weekly" picks the nearest expiry (default).
EXPIRY_TYPE = "weekly"

# IV_DIRECTION_THRESHOLD: min IV change (absolute) to be classified as rising/falling.
# NIFTY IV typically ranges 10–25; 0.3 is a reasonable noise floor.
IV_DIRECTION_THRESHOLD = 0.3

# ─── DAILY LOSS CIRCUIT BREAKER ────────────────────────
# If cumulative realised PnL for the day drops below this (negative ₹),
# the bot will refuse to enter any new trades for the rest of the session.
# Set to 0 to disable.
DAILY_LOSS_LIMIT = -5000          # e.g. ₹5,000 max daily loss

# ─── WEBSOCKET RECONNECT ────────────────────────────────
WS_RECONNECT_MAX_RETRIES = 10     # give up after this many consecutive failures
WS_RECONNECT_BASE_DELAY  = 5      # seconds — doubled each retry (exponential backoff)
WS_RECONNECT_MAX_DELAY   = 120    # cap backoff at 2 minutes

# ─── GEMINI PERFORMANCE TRACKING ───────────────────────
PERF_LOG_FILE  = "gemini_performance.csv"
TRADE_LOG_FILE = "trade_log.csv"
