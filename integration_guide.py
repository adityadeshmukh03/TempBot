# ─── INTEGRATION GUIDE (current state — reflects actual code) ────────────────
#
# This file documents how oi_analysis.py is wired into main.py and
# gemini_brain.py AS THEY ACTUALLY EXIST. No further manual changes needed.
# ──────────────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
# main.py — current wiring
# ══════════════════════════════════════════════════════════════════════════════
#
# run_analysis() execution order (correct):
#
#   1. calculate_indicators()         — compute price/volume indicators
#   2. detect_market_condition()      — TRENDING / RANGING / MIXED
#   3. run_oi_analysis()              — fetch full OI chain, PCR, max pain, walls
#   4. compute_confluence(            — fold OI score into indicator confluence
#          indicators, condition,
#          oi_result=_last_oi_result,
#          spot_price=spot_price)
#   5. fetch_option_data()            — Greeks, IV, IV Rank
#   6. enrich_with_option_data()      — merge Greeks into indicators dict
#   7. [confluence gate]              — skip Gemini if score badly missed
#   8. analyse(..., oi_result=_last_oi_result)  — Gemini adversarial audit
#
# KEY POINT: OI analysis runs BEFORE confluence so its score (0-3 pts)
# is included in the confluence total. Running it after (old bug) meant
# OI was never counted toward the entry gate.


# ══════════════════════════════════════════════════════════════════════════════
# gemini_brain.py — current wiring
# ══════════════════════════════════════════════════════════════════════════════
#
# OI data reaches Gemini via two paths:
#
#   Path A — build_audit_prompt() calls _format_oi_chain_section(oi_result)
#            which is defined natively inside gemini_brain.py.
#            format_oi_for_gemini() from oi_analysis.py is NOT used here.
#
#   Path B — analyse() accepts oi_result= kwarg and passes it through
#            to build_audit_prompt().
#
# _format_oi_chain_section() renders: PCR, max pain, nearest CE/PE walls,
# OI buildup bias. This is injected directly into the audit prompt.
#
# NOTE: format_oi_for_gemini() in oi_analysis.py is a standalone helper
# available if you ever want a richer OI block (includes per-category
# buildup lists). To switch, replace _format_oi_chain_section(oi_result)
# in build_audit_prompt() with:
#
#   from oi_analysis import format_oi_for_gemini
#   oi_section = format_oi_for_gemini(oi_result, indicators['current_price'])


# ══════════════════════════════════════════════════════════════════════════════
# indicators.py — compute_confluence() signature
# ══════════════════════════════════════════════════════════════════════════════
#
# def compute_confluence(indicators, condition, oi_result=None, spot_price=None)
#
# OI confluence (compute_oi_confluence) is called inside compute_confluence
# and its score is folded into the total. Always pass both kwargs:
#
#   confluence = compute_confluence(
#       indicators, condition,
#       oi_result=_last_oi_result,
#       spot_price=spot_price
#   )
#
# If oi_result is None (e.g. chain fetch failed), OI contributes 0 pts
# and no hard-block is set. Trading continues on indicator confluence alone.


# ══════════════════════════════════════════════════════════════════════════════
# OI hard-block vs Gemini VETO — two separate gates
# ══════════════════════════════════════════════════════════════════════════════
#
# OI hard-block (compute_oi_confluence):
#   Fires when PCR > 1.3 AND buildup_bias == "bearish", OR
#   price is within 30 pts of a CE OI wall.
#   Sets confluence['met'] = False regardless of score.
#   Trade is blocked before Gemini is ever called.
#
# Gemini VETO (final_signal in gemini_brain.py):
#   Fires when Gemini's audit finds a structural reason to kill the trade
#   (rejection wick, bear engulf, IV expensive, etc.).
#   Sets signal = WAIT after confluence has already passed.
#
# Both must pass for an ENTER signal to be produced.