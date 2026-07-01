"""Configure a swing-only GBPUSD engine with two-position capacity.

Entry hierarchy:
- D1 and H4 establish the swing direction.
- M1 and M5 must agree with that direction as short-term trend observers.
- A completed M30 candle is the actual entry trigger.
- Intraday order book is disabled.
- Maximum two same-direction swing positions.
- H4 and D1 volatility determine SL/TP.

Run after the completed-candle patch.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"
CONFIG = ROOT / "mt5_ai_bridge" / "config.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Could not find patch target: {label}")
    return text.replace(old, new, 1)


def patch_config() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    replacements = {
        'MAX_OPEN_POSITIONS", "4"': 'MAX_OPEN_POSITIONS", "2"',
        'MAX_OPEN_POSITIONS", "7"': 'MAX_OPEN_POSITIONS", "2"',
        'MAX_SAME_DIRECTION", "4"': 'MAX_SAME_DIRECTION", "2"',
        'MAX_SAME_DIRECTION", "7"': 'MAX_SAME_DIRECTION", "2"',
        'MIN_SAME_DIRECTION", "3"': 'MIN_SAME_DIRECTION", "1"',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    CONFIG.write_text(text, encoding="utf-8")


def patch_app() -> None:
    text = APP.read_text(encoding="utf-8")

    old = '''    timeframes = (settings.timeframe, settings.trend_tf_mid,
                  settings.swing_tf_high, settings.swing_tf_higher)
    decisions = {tf: decide(tf)[1] for tf in timeframes}
    state = _dual_engine_state(decisions, settings)
'''
    new = '''    # Swing-only entry stack: M1/M5 observe short-term trend, M30 triggers,
    # H4/D1 define the higher-timeframe swing direction.
    entry_tf = "M30"
    observer_tfs = ("M1", "M5")
    timeframes = observer_tfs + (entry_tf, settings.swing_tf_high,
                                 settings.swing_tf_higher)
    decisions = {tf: decide(tf)[1] for tf in timeframes}

    h4_decision = decisions[settings.swing_tf_high]
    d1_decision = decisions[settings.swing_tf_higher]
    m30_decision = decisions[entry_tf]
    m1_decision = decisions["M1"]
    m5_decision = decisions["M5"]

    swing_bias = trend_bias(h4_decision.signal, d1_decision.signal)
    observers_agree = bool(
        swing_bias is not None
        and m1_decision.signal is swing_bias
        and m5_decision.signal is swing_bias
    )
    m30_trigger = bool(swing_bias is not None and m30_decision.signal is swing_bias)
    swing_valid = bool(swing_bias is not None and observers_agree and m30_trigger)
    swing_conf = (min(h4_decision.confidence, d1_decision.confidence,
                      m1_decision.confidence, m5_decision.confidence,
                      m30_decision.confidence)
                  if swing_valid else 0.0)
    state = {
        "swing": {
            "valid": swing_valid,
            "bias": swing_bias,
            "confidence": swing_conf,
            "reason": ("D1/H4 direction aligned; M1/M5 trend observers and "
                       "completed M30 entry trigger agree."
                       if swing_valid else
                       "Waiting for D1/H4, M1/M5 and completed M30 alignment."),
        }
    }
'''
    text = replace_once(text, old, new, "M1/M5 observation and M30 trigger")

    old = '''        strong = setup["confidence"] >= settings.strong_trend_confidence
        desired = desired_positions(book, strong)
'''
    new = '''        # Never open more than two swing positions, and never open opposite
        # exposure. A second position is only allowed while the entire stack stays
        # aligned with the current completed-candle signal.
        desired = min(2, settings.max_open_positions)
'''
    text = replace_once(text, old, new, "two-position cap")

    old = '''        snap = decide(snap_tf)[0]
        stops = atr_stops((snap or {}).get("atr"), pip, atr_cfg) \\
            if atr_cfg.enabled else None
        base_sl, base_tp = stops if stops else (book.sl_pips, book.tp_pips)
'''
    new = '''        h4_snap = decide(settings.swing_tf_high)[0] or {}
        d1_snap = decide(settings.swing_tf_higher)[0] or {}
        h4_atr = float(h4_snap.get("atr") or 0.0)
        d1_atr = float(d1_snap.get("atr") or 0.0)
        if atr_cfg.enabled and h4_atr > 0:
            # H4 controls the trade structure; D1 prevents the stop from being too
            # tight during a broad daily-volatility regime.
            sl_price = max(1.25 * h4_atr, 0.35 * d1_atr)
            base_sl = sl_price / pip
            base_sl = max(atr_cfg.min_sl_pips,
                          min(base_sl, atr_cfg.max_sl_pips))
            # D1/H4 volatility ratio adjusts the target, capped at 2R.
            regime_ratio = (d1_atr / h4_atr) if h4_atr else 1.0
            reward_r = max(1.25, min(2.0, 1.25 + 0.15 * regime_ratio))
            base_tp = base_sl * reward_r
        else:
            base_sl, base_tp = book.sl_pips, book.tp_pips
'''
    text = replace_once(text, old, new, "H4/D1 volatility stops and targets")

    old = '''    # Swing is evaluated first; intraday may still open alongside it when both
    # engines point the same way and shared account limits permit.
    open_engine("swing", state["swing"], swing_book,
                settings.swing_tf_high, settings.swing_risk_percent)
    open_engine("intraday", state["intraday"], intraday_book,
                settings.timeframe, settings.intraday_risk_percent)
'''
    new = '''    # Swing only. M30 is the entry trigger, while H4/D1 control direction and
    # volatility-based exits. The intraday order book is deliberately disabled.
    open_engine("swing", state["swing"], swing_book,
                "M30", settings.swing_risk_percent)
'''
    text = replace_once(text, old, new, "disable intraday book")

    APP.write_text(text, encoding="utf-8")


def main() -> None:
    patch_config()
    patch_app()
    print("Two-position M30 swing patch complete. Run: python -m pytest -q")


if __name__ == "__main__":
    main()
