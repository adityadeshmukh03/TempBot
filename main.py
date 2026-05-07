# main.py

import time
import threading
from datetime import datetime
from kiteconnect import KiteTicker
import logging

from kite_client import (
    load_access_token,
    get_underlying_ltp,
    get_underlying_instrument_token,
)
from candle_builder import CandleBuilder
from indicators import (
    calculate_indicators,
    detect_market_condition,
    compute_confluence,
    enrich_with_option_data,
)
from gemini_brain import analyse, compute_trade_levels
from option_data import fetch_option_data, compare_iv_direction
from oi_analysis import run_oi_analysis
from hard_filters import run_cheap_filters, run_option_filters
from position_sizing import calculate_position_size, format_sizing_summary
from performance_tracker import (
    log_signal as log_performance_signal,
    log_outcome as log_performance_outcome,
)
from market_context import get_market_context, format_context_summary
from regime_detector import detect_regime, format_regime_summary
from liquidity_logic import detect_liquidity_events, format_liquidity_summary
from strategy_core import evaluate_strategy_edge
from strike_selector import detect_direction, resolve_instrument
from execution_intelligence import analyse_execution_quality, apply_execution_to_signal
from self_optimizer import get_runtime_adjustments, apply_runtime_adjustments
from order_manager import OrderManager
from order_sync import OrderSynchronizer
from sl_manager import StopLossManager
from target_manager import TargetManager
from trailing_manager import TrailingStopManager
from execution_engine import ExecutionEngine
from signal_engine import SignalEngine
from state_engine import TradingEngineState
from trading_engine import TradingEngine
from events import EntryEvent, EventBus, ExitEvent, SignalEvent, TickEvent
from config_validation import validate_config
from health_monitor import HealthMonitor
from logging_config import setup_logging
from ws_manager import WebSocketWatchdog
from persistence import StateStore
import position_tracker as pt
import config


validate_config(config)
logger = setup_logging(getattr(config, "LOG_DIR", "logs"), level=logging.INFO)


# ─── STARTUP ──────────────────────────────────────────────────────────────────
kite = load_access_token()
builder = CandleBuilder(interval_minutes=config.CANDLE_INTERVAL)
state_store = StateStore(getattr(config, "STATE_DB_PATH", "state/bot_state.sqlite"))
order_manager = OrderManager(kite, logger=logger)
order_sync = OrderSynchronizer(kite, logger=logger)
sl_manager = StopLossManager()
target_manager = TargetManager()
trailing_manager = TrailingStopManager(
    enabled=getattr(config, "TRAILING_SL_ENABLED", True),
    trail_after_r=getattr(config, "TRAIL_AFTER_R", 1.0),
    trail_by_r=getattr(config, "TRAIL_BY_R", 0.5),
)
ws_watchdog = WebSocketWatchdog(getattr(config, "WS_STALE_AFTER_SECONDS", 15), logger=logger)
event_bus = EventBus(logger=logger)
health_monitor = HealthMonitor(
    max_latency_ms=getattr(config, "MAX_LATENCY_MS", 2500),
    api_failure_threshold=getattr(config, "API_FAILURE_THRESHOLD", 5),
    logger=logger,
)
engine_state = TradingEngineState()
trading_engine = TradingEngine(engine_state, event_bus=event_bus, logger=logger)
signal_engine = SignalEngine(kite, config, logger=logger, health_monitor=health_monitor)
execution_engine = ExecutionEngine(
    order_manager,
    order_sync,
    sl_manager,
    target_manager,
    trailing_manager,
    state_store,
    pt,
    config,
    logger=logger,
)

restored_position = state_store.load_open_position()
if restored_position:
    execution_engine.register_restored_position(restored_position)

restored_daily_risk = state_store.load_daily_risk()
if restored_daily_risk:
    pt.restore_daily_risk(restored_daily_risk)

try:
    underlying_token = get_underlying_instrument_token(config.INSTRUMENT)
    print(f"[UNDERLYING] {config.INSTRUMENT} | Token: {underlying_token}")
except Exception as e:
    underlying_token = None
    print(f"[UNDERLYING] Token lookup failed: {e}")

try:
    _bootstrap_spot = get_underlying_ltp(config.INSTRUMENT)
    engine_state.set_spot(_bootstrap_spot)
    _bootstrap_instrument = resolve_instrument(kite, config.INSTRUMENT, "CE", _bootstrap_spot)
    if _bootstrap_instrument:
        engine_state.set_instrument(_bootstrap_instrument, "CE")
        symbol = _bootstrap_instrument["tradingsymbol"]
        print(f"[DIRECTION] Bootstrap subscription uses {symbol}; direction is re-evaluated each candle.")
        builder.preload_historical(kite, instrument_token=_bootstrap_instrument["instrument_token"])
except Exception as e:
    print(f"[DIRECTION] Bootstrap instrument lookup failed: {e}")

# ─── ANALYSIS STATE ───────────────────────────────────────────────────────────


def fetch_underlying_spot():
    """Fetch real underlying spot. Do not use option premium as spot."""
    try:
        engine_state.set_spot(get_underlying_ltp(config.INSTRUMENT))
    except Exception as e:
        health_monitor.record_api_failure("kite_spot", e)
        print(f"[SPOT] Underlying quote failed: {e}")
    return engine_state.snapshot().last_spot_price


def run_analysis(option_ltp):
    signal_started_at = health_monitor.timer()
    allowed, block_reason = health_monitor.trading_allowed()
    if not allowed:
        print(f"[API CIRCUIT] {block_reason}")
        return

    # ── CIRCUIT BREAKER — daily loss limit ──────────────────────────────────
    if pt.is_circuit_open():
        print(f"[CIRCUIT BREAKER] No new entries today. {pt.get_daily_summary()}")
        return

    # ── POSITION GUARD — already in a trade ─────────────────────────────────
    if pt.is_in_position():
        snap = pt.get_position_snapshot()
        print(
            f"[POSITION] Already in trade — "
            f"{snap['symbol']} @ {snap['entry_price']} "
            f"(entered {snap['entry_time'].strftime('%H:%M') if snap['entry_time'] else '?'}). "
            f"Skipping new signal."
        )
        return

    candles = builder.get_todays_candles()

    if builder.candle_count() < 14:
        print(f"[WAIT] Only {builder.candle_count()} candles - need 14 minimum")
        return

    indicators = calculate_indicators(candles)
    if indicators is None:
        return

    condition = detect_market_condition(indicators)

    # ── Pass 1: Cheap filters ────────────────────────────────────────────────
    blocked, filter_reason = run_cheap_filters(
        indicators=indicators,
        candle_count=builder.candle_count()
    )
    if blocked:
        print(f"[FILTER BLOCK] {filter_reason}")
        return

    spot_price = fetch_underlying_spot()
    if spot_price is None:
        print("[SPOT] No underlying spot available - skipping analysis")
        return

    try:
        oi_result, market_context = signal_engine.fetch_market_inputs(
            spot_price,
            underlying_token,
            run_oi_analysis,
            get_market_context,
        )
        engine_state.set_oi_result(oi_result)
    except Exception as e:
        health_monitor.record_api_failure("market_inputs", e)
        print(f"[API BLOCK] Market input fetch failed: {e}")
        return

    recent_for_context = builder.get_recent_candles(count=12)

    def _resolve_and_fetch(direction_to_fetch):
        instrument = resolve_instrument(kite, config.INSTRUMENT, direction_to_fetch, spot_price)
        if instrument is None:
            print("[STRIKE] Could not resolve instrument - skipping candle.")
            return None, None

        snap = engine_state.snapshot()
        previous_iv = snap.prev_iv if snap.token == instrument["instrument_token"] else None
        option_data_for_direction = fetch_option_data(
            kite,
            instrument_token=instrument["instrument_token"],
            symbol=instrument["tradingsymbol"],
            underlying_name=config.INSTRUMENT,
            strike=instrument["strike"],
            option_type=direction_to_fetch
        )
        if option_data_for_direction:
            option_data_for_direction['iv_direction'] = compare_iv_direction(
                option_data_for_direction.get('iv'), previous_iv
            )
        return instrument, option_data_for_direction

    preliminary_direction = detect_direction(
        market_context, indicators, {"hard_block": False}, oi_result
    )
    if preliminary_direction is None:
        print("[DIRECTION] Market context mixed or blocked - no trade this candle.")
        return

    instrument, option_data = _resolve_and_fetch(preliminary_direction)
    if instrument is None:
        return

    liquidity = detect_liquidity_events(
        recent_for_context,
        market_context=market_context,
        oi_result=oi_result,
        direction=preliminary_direction
    )
    regime = detect_regime(
        indicators=indicators,
        market_context=market_context,
        option_data=option_data,
        oi_result=oi_result,
        recent_candles=recent_for_context,
        liquidity=liquidity
    )

    direction = detect_direction(market_context, indicators, regime, oi_result)
    if direction is None:
        print("[DIRECTION] Market context mixed or regime blocked - no trade this candle.")
        return

    if direction != preliminary_direction:
        instrument, option_data = _resolve_and_fetch(direction)
        if instrument is None:
            return
        liquidity = detect_liquidity_events(
            recent_for_context,
            market_context=market_context,
            oi_result=oi_result,
            direction=direction
        )
        # BUG FIX: reuse existing indicators/oi_result — no extra API calls needed for regime
        regime = detect_regime(
            indicators=indicators,
            market_context=market_context,
            option_data=option_data,
            oi_result=oi_result,
            recent_candles=recent_for_context,
            liquidity=liquidity
        )
        direction = detect_direction(market_context, indicators, regime, oi_result)
        if direction is None:
            print("[DIRECTION] Regime blocked after final instrument resolution - no trade this candle.")
            return
        if direction != instrument["direction"]:
            print("[DIRECTION] Direction changed after final regime check - skipping candle.")
            return

    if engine_state.snapshot().token != instrument["instrument_token"]:
        engine_state.set_prev_iv(None)
        engine_state.set_instrument(instrument, direction)
        symbol = instrument["tradingsymbol"]
        try:
            builder.preload_historical(kite, instrument_token=instrument["instrument_token"])
        except Exception as e:
            health_monitor.record_api_failure("historical_preload", e)
            print(f"[INSTRUMENT] Historical preload failed for {symbol}: {e}")
        trading_engine.ensure_subscribed(engine_state.snapshot().ws, instrument["instrument_token"])
        print(
            f"[INSTRUMENT] Switched to {symbol}. "
            "Skipping this candle so indicators, LTP, and order levels come from the same contract."
        )
        return
    else:
        symbol = instrument["tradingsymbol"]
    engine_state.set_instrument(instrument, direction)

    strike = instrument["strike"]
    print(f"[INSTRUMENT] {symbol} | Strike: {strike} | Direction: {direction} | Delta: {instrument['delta']}")

    if option_data:
        engine_state.set_prev_iv(option_data.get('iv'))

    enriched = enrich_with_option_data(indicators, option_data)
    _print_option_summary(option_data)

    # ── Pass 2: Option-data-dependent filters ─────────────────────────────────
    blocked, filter_reason = run_option_filters(option_data=option_data)
    if blocked:
        print(f"[FILTER BLOCK] {filter_reason}")
        return

    strategy_edge = evaluate_strategy_edge(
        indicators=indicators,
        market_context=market_context,
        regime=regime,
        liquidity=liquidity,
        oi_result=oi_result,
        direction=direction
    )

    print(f"[CONTEXT] {format_context_summary(market_context)}")
    print(f"[REGIME] {format_regime_summary(regime)}")
    print(f"[LIQUIDITY] {format_liquidity_summary(liquidity)}")

    if strategy_edge.get("blocked"):
        print(f"[STRATEGY BLOCK] {strategy_edge.get('block_reason')}")
        return

    confluence = compute_confluence(
        indicators,
        condition,
        oi_result=oi_result,
        spot_price=spot_price,
        direction=direction
    )
    optimizer_adjustments = get_runtime_adjustments()
    confluence = apply_runtime_adjustments(
        confluence=confluence,
        regime=regime,
        strategy_edge=strategy_edge,
        adjustments=optimizer_adjustments
    )

    print(f"\n[ANALYSIS] {datetime.now().strftime('%H:%M')} | "
          f"Candles: {builder.candle_count()} | "
          f"Option LTP: {option_ltp} | "
          f"Spot: {spot_price} | "
          f"Condition: {condition} | "
          f"Regime: {regime.get('regime')} | "
          f"Confluence: {confluence['score']}/{confluence['total']} | "
          f"Required: {confluence['required']} | "
          f"Met: {confluence['met']} | "
          f"{pt.get_daily_summary()}")

    if confluence.get('oi_block'):
        print(f"[OI BLOCK] {confluence.get('oi_block_reason')}")
        return

    if confluence['score'] < confluence['required'] - 2:
        print("[SKIP] Confluence too low - skipping Gemini call")
        return

    # BUG FIX: warn explicitly when falling back to option-contract key levels
    key_levels = _get_underlying_key_levels(market_context, builder.get_key_levels())
    daily = (market_context or {}).get("daily", {})
    if not daily.get("pdh") and not daily.get("pdl"):
        logger.warning(
            "market_context daily levels unavailable — falling back to candle builder "
            "key levels (option contract prices, not underlying index). "
            "PDH/PDL sent to Gemini may be inaccurate."
        )

    recent      = builder.get_recent_candles(count=6)
    option_info = {
        "symbol":          symbol,
        "ltp":             option_ltp,
        "underlying_spot": spot_price,
    }

    try:
        signal = analyse(
            enriched, key_levels, recent, option_info,
            condition, confluence,
            oi_result=oi_result,
            market_context=market_context,
            regime_result=regime,
            liquidity=liquidity,
            direction=direction
        )
    except Exception as e:
        health_monitor.record_api_failure("gemini", e)
        fallback_levels = compute_trade_levels(
            enriched,
            key_levels,
            recent,
            underlying_spot=spot_price,
            direction=direction,
        )
        signal = signal_engine.deterministic_fallback_signal(confluence, fallback_levels, str(e))

    if signal and "Gemini_Audit_Unavailable" in signal.get("audit_flags", []):
        health_monitor.record_api_failure("gemini", signal.get("veto_reason", "Gemini audit unavailable"))

    event_bus.publish(SignalEvent(datetime.now(), {"symbol": symbol, "direction": direction, "signal": signal or {}}))

    if signal:
        execution = analyse_execution_quality(option_data, desired_side="BUY")
        signal = apply_execution_to_signal(signal, execution)
        signal["execution_quality"]   = execution.get("quality", "")
        signal["spread_pct"]          = execution.get("spread_pct", "")
        signal["adaptive_adjustment"] = confluence.get("adaptive_adjustment", 0)
        signal["strategy_edge"]       = strategy_edge.get("edge_id", "")
        signal["direction"]           = direction

        sizing = {
            "lots":         0,
            "units":        0,
            "max_loss":     0,
            "blocked":      True,
            "block_reason": "No ENTER signal",
        }

        if signal.get("signal") == "ENTER":
            latency_ms = health_monitor.record_latency(signal_started_at, label="signal_cycle")
            if not health_monitor.latency_allows_entry():
                signal["signal"]          = "WAIT"
                signal["confidence"]      = "low"
                signal["decision_reason"] = (
                    f"{signal.get('decision_reason', '')} | LATENCY_BLOCK - "
                    f"{latency_ms}ms > {getattr(config, 'MAX_LATENCY_MS', 2500)}ms"
                )
                _print_signal(signal)
                log_signal(signal, option_ltp, option_data)
                return

            sizing = calculate_position_size(
                signal.get("entry_price", 0),
                signal.get("stop_loss", 0)
            )
            print(format_sizing_summary(sizing))

            if sizing.get("blocked"):
                signal["signal"]          = "WAIT"
                signal["confidence"]      = "low"
                signal["decision_reason"] = (
                    f"{signal.get('decision_reason', '')} | SIZING_BLOCK - "
                    f"{sizing.get('block_reason', '')}"
                )
            else:
                entry_order = order_manager.place_entry(
                    symbol=symbol,
                    exchange=instrument.get("exchange", config.INSTRUMENT_EXCHANGE.get(config.INSTRUMENT, "NFO")),
                    quantity=sizing["units"],
                    price=signal["entry_price"] if getattr(config, "ENTRY_ORDER_TYPE", "MARKET") == "LIMIT" else None,
                )
                if not entry_order.ok:
                    signal["signal"]          = "WAIT"
                    signal["confidence"]      = "low"
                    signal["decision_reason"] = (
                        f"{signal.get('decision_reason', '')} | ORDER_REJECTED - {entry_order.error}"
                    )
                    logger.error("Entry order failed: %s", entry_order)
                    _print_signal(signal)
                    return

                fill_price = entry_order.average_price or signal["entry_price"]
                signal["entry_price"] = fill_price

                opened = pt.open_position(
                    entry_price=fill_price,
                    stop_loss=signal["stop_loss"],
                    target_1=signal["target_1"],
                    target_2=signal["target_2"],
                    lots=sizing["lots"],
                    units=sizing["units"],
                    symbol=symbol,
                )
                if not opened:
                    print("[POSITION] Duplicate ENTER blocked by tracker. Skipping.")
                    return

                sl_manager.register(symbol, signal["stop_loss"], sizing["units"])
                target_manager.register(
                    symbol,
                    signal["target_1"],
                    signal["target_2"],
                    sizing["units"],
                    getattr(config, "PARTIAL_EXIT_PCT", 0.5),
                )
                trailing_manager.register(symbol, fill_price, signal["stop_loss"])

                if getattr(config, "ENABLE_SL_ORDER", False):
                    sl_result = order_manager.place_stop_loss(
                        symbol=symbol,
                        exchange=instrument.get("exchange", config.INSTRUMENT_EXCHANGE.get(config.INSTRUMENT, "NFO")),
                        quantity=sizing["units"],
                        trigger_price=signal["stop_loss"],
                    )
                    if not sl_result.ok:
                        logger.error("SL-M placement failed: %s", sl_result)
                    else:
                        sl_manager.register(symbol, signal["stop_loss"], sizing["units"], order_id=sl_result.order_id)

                state_store.save_open_position(pt.get_position_snapshot())
                event_bus.publish(EntryEvent(datetime.now(), {"symbol": symbol, "units": sizing["units"]}))

        _print_signal(signal)
        log_signal(signal, option_ltp, option_data)
        log_performance_signal(
            signal=signal,
            sizing=sizing,
            option_data=option_data,
            oi_result=oi_result,
            indicators=enriched,
            symbol=symbol,
            direction=direction
        )


# ─── STRUCTURE HELPERS ────────────────────────────────────────────────────────

def _get_underlying_key_levels(market_context, fallback_levels):
    daily = (market_context or {}).get("daily", {})
    return {
        "PDH": daily.get("pdh") or fallback_levels.get("PDH"),
        "PDL": daily.get("pdl") or fallback_levels.get("PDL"),
        "PDC": daily.get("pdc") or fallback_levels.get("PDC"),
    }


# ─── TERMINAL OUTPUT ──────────────────────────────────────────────────────────

def _print_option_summary(option_data):
    if not option_data:
        print("[OPTIONS] No option data available this candle")
        return

    def _f(v, default="N/A"):
        return v if v is not None else default

    print(f"[OPTIONS] "
          f"IV: {_f(option_data.get('iv'))} | "
          f"IV Rank: {_f(option_data.get('iv_rank'))} | "
          f"IV Dir: {_f(option_data.get('iv_direction'))} | "
          f"Delta: {_f(option_data.get('delta'))} | "
          f"Theta: {_f(option_data.get('theta'))} | "
          f"DTE: {_f(option_data.get('days_to_expiry'))} | "
          f"OI: {_f(option_data.get('oi'))} | "
          f"OI Chg: {_f(option_data.get('oi_change'))}")


def _print_signal(signal):
    print("\n===== GEMINI SIGNAL =====")
    print(f"  Condition      : {signal['condition']}")
    if signal.get("regime"):
        print(f"  Regime         : {signal['regime']} ({signal.get('regime_confidence', '')})")
    if signal.get("market_alignment"):
        print(f"  HTF Alignment  : {signal['market_alignment']}")
    print(f"  Signal         : {signal['signal']}")
    print(f"  Confidence     : {signal['confidence']}")
    print(f"  Audit Verdict  : {signal['audit_verdict']}")
    print(f"  Confluence     : {signal['confluence_score']} (met: {signal['confluence_met']})")
    print(f"  Entry          : {signal['entry_price']}")
    print(f"  Target 1       : {signal['target_1']}")
    print(f"  Target 2       : {signal['target_2']}")
    print(f"  Stop Loss      : {signal['stop_loss']}")
    if signal.get("execution_quality"):
        print(f"  Execution      : {signal['execution_quality']} | Spread%: {signal.get('spread_pct')}")
    print(f"  Reason         : {signal['decision_reason']}")
    print(f"  Auditor Checked: {signal['what_auditor_checked']}")
    if signal.get('veto_reason'):
        print(f"  Veto Reason    : {signal['veto_reason']}")
    if signal.get('warn_reasons'):
        print(f"  Warn Reasons   : {signal['warn_reasons']}")
    if signal.get('near_resistance'):
        print(f"  Resistance     : {signal['resistance_level']}")
    print("=========================\n")


# ─── CSV TRADE LOG ────────────────────────────────────────────────────────────

def log_signal(signal, ltp, option_data=None):
    import csv, os

    file   = config.TRADE_LOG_FILE
    exists = os.path.exists(file)

    od             = option_data or {}
    iv             = od.get('iv')
    iv_rank        = od.get('iv_rank')
    delta          = od.get('delta')
    theta          = od.get('theta')
    days_to_expiry = od.get('days_to_expiry')

    with open(file, "a", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow([
                "time", "ltp", "direction", "condition", "signal",
                "confidence", "audit_verdict",
                "confluence", "confluence_required", "confluence_met",
                "entry", "target1", "target2", "stop_loss", "risk_points",
                "decision_reason", "veto_reason", "warn_reasons",
                "underlying_spot",
                "iv", "iv_rank", "delta", "theta", "days_to_expiry",
                "regime", "market_alignment", "liquidity_events",
                "execution_quality", "spread_pct", "adaptive_adjustment",
                "strategy_edge"
            ])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            ltp,
            signal.get('direction', ''),
            signal['condition'],
            signal['signal'],
            signal['confidence'],
            signal.get('audit_verdict', ''),
            signal['confluence_score'],
            signal.get('confluence_required', ''),
            signal['confluence_met'],
            signal['entry_price'],
            signal['target_1'],
            signal['target_2'],
            signal['stop_loss'],
            signal.get('risk_points', ''),
            signal.get('decision_reason', ''),
            signal.get('veto_reason', ''),
            signal.get('warn_reasons', ''),
            signal.get('underlying_spot', ''),
            iv, iv_rank, delta, theta, days_to_expiry,
            signal.get('regime', ''),
            signal.get('market_alignment', ''),
            "|".join(signal.get('liquidity_events', [])),
            signal.get('execution_quality', ''),
            signal.get('spread_pct', ''),
            signal.get('adaptive_adjustment', ''),
            signal.get('strategy_edge', '')
        ])


# ─── WEBSOCKET CALLBACKS ──────────────────────────────────────────────────────

option_ltp = {"value": 0}


def _ensure_subscribed(ws, new_token):
    trading_engine.ensure_subscribed(ws, new_token)


def _save_daily_risk_state():
    state_store.save_daily_risk(pt.get_daily_risk_snapshot())


def _execute_full_exit(ltp, exit_reason):
    if not pt.is_in_position():
        return False

    snap           = pt.get_position_snapshot()
    symbol         = snap.get("symbol")
    state_snapshot = engine_state.snapshot()
    exchange       = (state_snapshot.current_instrument or {}).get(
        "exchange",
        config.INSTRUMENT_EXCHANGE.get(config.INSTRUMENT, "NFO")
    )
    entry_time  = snap.get("entry_time")
    lots        = snap.get("lots", 0)
    entry_price = snap.get("entry_price")
    units       = snap.get("units", 0)

    if not symbol or not units or not ltp:
        print(f"[EXECUTION BLOCK] Cannot exit {exit_reason}: missing symbol/quantity/ltp")
        return False

    exit_order = order_manager.place_exit(
        symbol=symbol,
        exchange=exchange,
        quantity=units,
        price=ltp if getattr(config, "EXIT_ORDER_TYPE", "MARKET") == "LIMIT" else None,
    )
    if not exit_order.ok:
        print(f"[EXECUTION BLOCK] Exit order failed: {exit_order.error}")
        logger.error("Exit order failed: %s", exit_order)
        return False

    summary = pt.close_position(exit_price=ltp, exit_reason=exit_reason)
    _save_daily_risk_state()
    state_store.delete("open_position")
    if symbol:
        sl_manager.clear(symbol)
        target_manager.clear(symbol)
        trailing_manager.clear(symbol)
    if summary and entry_time:
        log_performance_outcome(
            entry_time=entry_time.strftime("%H:%M"),
            exit_price=ltp,
            exit_reason=exit_reason,
            lots=lots,
            entry_price=entry_price,
        )
    event_bus.publish(ExitEvent(datetime.now(), {"symbol": symbol, "reason": exit_reason, "partial": False}))
    return True


def _check_open_position_exit(ltp):
    if not pt.is_in_position():
        return False

    snap           = pt.get_position_snapshot()
    symbol         = snap.get("symbol")
    state_snapshot = engine_state.snapshot()
    exchange       = (state_snapshot.current_instrument or {}).get(
        "exchange",
        config.INSTRUMENT_EXCHANGE.get(config.INSTRUMENT, "NFO")
    )
    now_ts = time.time()
    if now_ts - state_snapshot.last_reconcile_at >= getattr(config, "BROKER_RECONCILE_SECONDS", 30):
        engine_state.set_reconcile_time(now_ts)
        if symbol:
            sync = order_sync.reconcile_symbol(symbol, snap.get("units", 0))
            if not sync.get("in_sync"):
                health_monitor.record_api_failure("broker_reconcile", "broker/internal mismatch")
                logger.error("Broker reconciliation drift: %s", sync)
                print(f"[RECONCILE] Broker/internal mismatch: {sync}")
                if sync.get("actual_qty") == 0 and snap.get("units", 0) > 0:
                    print("[RECONCILE] Broker is flat; clearing internal open position.")
                    summary = pt.close_position(exit_price=ltp, exit_reason="BROKER_SYNC_EXIT")
                    _save_daily_risk_state()
                    state_store.delete("open_position")
                    sl_manager.clear(symbol)
                    target_manager.clear(symbol)
                    trailing_manager.clear(symbol)
                    if summary and snap.get("entry_time"):
                        log_performance_outcome(
                            entry_time=snap["entry_time"].strftime("%H:%M"),
                            exit_price=ltp,
                            exit_reason="BROKER_SYNC_EXIT",
                            lots=snap.get("lots", 0),
                            entry_price=snap.get("entry_price"),
                        )
                    event_bus.publish(ExitEvent(datetime.now(), {"symbol": symbol, "reason": "BROKER_SYNC_EXIT", "partial": False}))
                    return True

    trailing_sl = trailing_manager.update(symbol, ltp) if symbol else None
    if trailing_sl and snap.get("stop_loss") and trailing_sl > snap.get("stop_loss"):
        pt.update_stop_loss(trailing_sl)
        managed_sl = sl_manager.update(symbol, trailing_sl) if symbol else None
        if managed_sl and getattr(config, "ENABLE_SL_ORDER", False):
            order_id = managed_sl.get("order_id")
            if order_id and not order_manager.modify_stop_loss(order_id, trailing_sl):
                logger.error("Broker SL modification failed for %s -> %s", symbol, trailing_sl)
        snap = pt.get_position_snapshot()
        state_store.save_open_position(snap)

    stop_loss   = snap.get("stop_loss")
    target_1    = snap.get("target_1")
    target_2    = snap.get("target_2")
    target_mode = getattr(config, "AUTO_EXIT_TARGET", "TARGET_1")
    units       = snap.get("units", 0)

    if getattr(config, "PARTIAL_EXITS_ENABLED", False) and target_mode == "TARGET_2":
        qty_to_exit, partial_reason = target_manager.exit_quantity(symbol, ltp)
        if qty_to_exit and qty_to_exit < units:
            partial_order = order_manager.place_exit(
                symbol=symbol,
                exchange=exchange,
                quantity=qty_to_exit,
                price=ltp if getattr(config, "EXIT_ORDER_TYPE", "MARKET") == "LIMIT" else None,
            )
            if not partial_order.ok:
                print(f"[EXECUTION BLOCK] Partial exit order failed: {partial_order.error}")
                logger.error("Partial exit order failed: %s", partial_order)
                return False
            pt.reduce_position(exit_price=ltp, exit_reason=partial_reason, units_to_exit=qty_to_exit)
            _save_daily_risk_state()
            state_store.save_open_position(pt.get_position_snapshot())
            event_bus.publish(ExitEvent(datetime.now(), {"symbol": symbol, "reason": partial_reason, "partial": True}))
            return True

    exit_reason = None
    if stop_loss is not None and ltp <= stop_loss:
        exit_reason = "STOP_LOSS"
    elif target_mode == "TARGET_2" and target_2 is not None and ltp >= target_2:
        exit_reason = "TARGET_2"
    elif target_1 is not None and ltp >= target_1:
        exit_reason = "TARGET_1"

    if not exit_reason:
        return False

    if (
        exit_reason == "STOP_LOSS"
        and getattr(config, "ENABLE_SL_ORDER", False)
        and symbol
        and (sl_manager.active.get(symbol) or {}).get("order_id")
    ):
        print("[SL] Broker SL order is active; waiting for broker fill/reconcile instead of sending duplicate exit.")
        return False

    return _execute_full_exit(ltp, exit_reason)


def _is_eod_exit_time():
    if not getattr(config, "AUTO_EOD_EXIT", True):
        return False
    try:
        cutoff = datetime.strptime(getattr(config, "EOD_EXIT_TIME", "15:20"), "%H:%M").time()
    except ValueError:
        cutoff = datetime.strptime("15:20", "%H:%M").time()
    return datetime.now().time() >= cutoff


def _maybe_eod_exit():
    if not pt.is_in_position() or not _is_eod_exit_time():
        return False
    ltp = option_ltp.get("value") or engine_state.snapshot().option_ltp
    if not ltp:
        print("[EOD] Open position exists but no option LTP is available for EOD exit.")
        return False
    print("[EOD] Auto square-off triggered.")
    return _execute_full_exit(ltp, "EOD")


def on_ticks(ws, ticks):
    ws_watchdog.mark_tick()
    health_monitor.mark_tick()

    for tick in ticks:
        if underlying_token and tick['instrument_token'] == underlying_token:
            engine_state.set_spot(tick.get('last_price'))

        state_snapshot = engine_state.snapshot()
        if state_snapshot.token is not None and tick['instrument_token'] == state_snapshot.token:
            ltp    = tick['last_price']
            volume = tick.get('volume_traded', 0)
            ts     = datetime.now()

            option_ltp["value"] = ltp
            engine_state.set_option_ltp(ltp)
            event_bus.publish(TickEvent(ts, {"token": state_snapshot.token, "ltp": ltp, "volume": volume}))
            builder.process_tick(ltp, volume, ts)

            if _check_open_position_exit(ltp):
                continue

            current_bucket = builder.last_bucket
            if current_bucket and engine_state.set_analysis_candle(current_bucket) and builder.candle_count() > 0:
                run_analysis(option_ltp["value"])


def on_connect(ws, response):
    engine_state.set_ws(ws)
    print("[CONNECTED] Subscribing to underlying...")
    if underlying_token:
        ws_watchdog.resubscribe(ws, [underlying_token], ws.MODE_LTP)
    _ensure_subscribed(ws, engine_state.snapshot().token)


def on_close(ws, code, reason):
    health_monitor.record_api_failure("websocket", f"closed code={code} reason={reason}")
    logger.warning("websocket closed code=%s reason=%s", code, reason)
    print(f"[DISCONNECTED] code={code} reason={reason}")


def on_error(ws, code, reason):
    health_monitor.record_api_failure("websocket", f"error code={code} reason={reason}")
    logger.error("websocket error code=%s reason=%s", code, reason)
    print(f"[WS ERROR] code={code} reason={reason}")


# ─── RECONNECT LOOP ───────────────────────────────────────────────────────────

def start_ticker():
    base_delay = config.WS_RECONNECT_BASE_DELAY
    max_delay  = config.WS_RECONNECT_MAX_DELAY
    max_tries  = config.WS_RECONNECT_MAX_RETRIES
    attempt    = 0

    while is_market_open():
        attempt += 1
        if attempt > max_tries:
            print(
                f"[WS] {max_tries} consecutive reconnect attempts failed. "
                f"Giving up — please restart the bot manually."
            )
            break

        if attempt > 1:
            delay = min(base_delay * (2 ** (attempt - 2)), max_delay)
            trading_engine.on_reconnect(attempt, "socket reconnect")
            print(f"[WS] Reconnect attempt {attempt}/{max_tries} — waiting {delay}s ...")
            time.sleep(delay)

        try:
            ticker = KiteTicker(config.KITE_API_KEY, kite.access_token)
            ticker.on_ticks   = on_ticks
            ticker.on_connect = on_connect
            ticker.on_close   = on_close
            ticker.on_error   = on_error

            print(f"[WS] Connecting (attempt {attempt}) ...")
            ticker.connect(threaded=False)

            print("[WS] Socket closed cleanly. Will reconnect if market still open.")
            attempt = 0

        except Exception as exc:
            health_monitor.record_api_failure("websocket", exc)
            trading_engine.on_reconnect(attempt, str(exc))
            print(f"[WS] Connection exception on attempt {attempt}: {exc}")

    print("[WS] Reconnect loop exited.")


# ─── MARKET HOURS ─────────────────────────────────────────────────────────────

def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    market_start = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_end   = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    while True:
        if not is_market_open():
            if not getattr(config, "WAIT_FOR_MARKET_OPEN", True):
                print("[INFO] Market is closed. Run between 9:15 AM - 3:30 PM on weekdays.")
                break
            print("[INFO] Market is closed. Waiting for next session...")
            while not is_market_open():
                time.sleep(60)

        print("[STARTING] Bot is live ...")

        ws_thread = threading.Thread(target=start_ticker, daemon=True)
        ws_thread.start()

        while is_market_open():
            _maybe_eod_exit()
            time.sleep(10)

        _maybe_eod_exit()
        print(f"[DONE] Market closed. {pt.get_daily_summary()}")

        break
