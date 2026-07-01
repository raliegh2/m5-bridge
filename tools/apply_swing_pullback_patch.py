"""Convert the dual engine into a completed-candle, swing-only pullback engine.

Run from the repository root after apply_closed_candle_patch.py:
    python tools/apply_swing_pullback_patch.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"
INDICATORS = ROOT / "mt5_ai_bridge" / "indicators.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Could not find patch target: {label}")
    return text.replace(old, new, 1)


def patch_indicators() -> None:
    text = INDICATORS.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '        "close": float(latest["close"]),\n',
        '        "open": float(latest["open"]),\n'
        '        "high": float(latest["high"]),\n'
        '        "low": float(latest["low"]),\n'
        '        "close": float(latest["close"]),\n',
        "OHLC snapshot fields",
    )
    INDICATORS.write_text(text, encoding="utf-8")


def patch_app() -> None:
    text = APP.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'log = get_logger("app")\n',
        'log = get_logger("app")\n\n'
        '_LAST_SWING_H4_SETUP = {}\n\n\n'
        'def _fresh_swing_setup(client, setup_time) -> bool:\n'
        '    key = id(client)\n'
        '    marker = str(setup_time)\n'
        '    if _LAST_SWING_H4_SETUP.get(key) == marker:\n'
        '        return False\n'
        '    _LAST_SWING_H4_SETUP[key] = marker\n'
        '    return True\n',
        "fresh H4 setup gate",
    )

    text = replace_once(
        text,
        '    swing_trigger = trend_bias(entry.signal, mid.signal) \\\n        if entry is not None and mid is not None else None\n'
        '    swing_valid = bool(swing_bias is not None and swing_trigger is swing_bias)\n',
        '    swing_valid = bool(swing_bias is not None)\n',
        "higher-timeframe swing bias",
    )

    text = replace_once(
        text,
        '    swing_conf = min(entry.confidence, mid.confidence,\n'
        '                     high.confidence, higher.confidence) \\\n        if swing_valid else 0.0\n',
        '    swing_conf = min(high.confidence, higher.confidence) \\\n        if swing_valid else 0.0\n',
        "swing confidence",
    )

    text = replace_once(
        text,
        '            "reason": ("H4 and D1 agree, with matching M30/M15 timing."\n'
        '                       if swing_valid else\n'
        '                       "Waiting for H4/D1 trend and M30/M15 timing to agree."),\n',
        '            "reason": ("H4 and D1 trend agree; waiting for H4 pullback/resumption."\n'
        '                       if swing_valid else\n'
        '                       "Waiting for H4 and D1 trend agreement."),\n',
        "swing reason",
    )

    marker = '    books = build_books(settings)\n'
    insertion = '''    h4_snap = decide(settings.swing_tf_high)[0] or {}
    swing_setup = state["swing"]
    if swing_setup["valid"]:
        side = swing_setup["bias"]
        if side is Signal.BUY:
            pullback = (h4_snap.get("low", float("inf")) <= h4_snap.get("ema_20", 0)
                        and h4_snap.get("close", 0) > h4_snap.get("ema_20", 0)
                        and h4_snap.get("close", 0) > h4_snap.get("open", 0))
        else:
            pullback = (h4_snap.get("high", float("-inf")) >= h4_snap.get("ema_20", 0)
                        and h4_snap.get("close", 0) < h4_snap.get("ema_20", 0)
                        and h4_snap.get("close", 0) < h4_snap.get("open", 0))
        swing_setup["valid"] = bool(pullback)
        swing_setup["reason"] = ("Fresh H4 EMA20 pullback resumed with D1 agreement."
                                  if pullback else "Waiting for H4 EMA20 pullback/resumption.")

'''
    text = replace_once(text, marker, insertion + marker, "H4 pullback filter")

    text = replace_once(
        text,
        '        strong = setup["confidence"] >= settings.strong_trend_confidence\n'
        '        desired = desired_positions(book, strong)\n',
        '        desired = 1\n'
        '        if name == "swing" and not _fresh_swing_setup(client, (decide(settings.swing_tf_high)[0] or {}).get("time")):\n'
        '            log.info("swing engine waiting: current H4 setup already consumed.")\n'
        '            return\n',
        "one-position fresh setup",
    )

    text = replace_once(
        text,
        '    open_engine("swing", state["swing"], swing_book,\n'
        '                settings.swing_tf_high, settings.swing_risk_percent)\n'
        '    open_engine("intraday", state["intraday"], intraday_book,\n'
        '                settings.timeframe, settings.intraday_risk_percent)\n',
        '    open_engine("swing", state["swing"], swing_book,\n'
        '                settings.swing_tf_high, settings.swing_risk_percent)\n',
        "disable intraday engine",
    )

    APP.write_text(text, encoding="utf-8")


def main() -> None:
    patch_indicators()
    patch_app()
    print("Swing-only pullback patch complete. Run: python -m pytest -q")


if __name__ == "__main__":
    main()
