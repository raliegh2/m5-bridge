"""Apply risk-matched exits for the swing-only pullback engine.

Run after the closed-candle and swing-pullback patches:
    python tools/apply_swing_exit_patch.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"
TRADE_MANAGER = ROOT / "mt5_ai_bridge" / "trade_manager.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Could not find patch target: {label}")
    return text.replace(old, new, 1)


def patch_app() -> None:
    text = APP.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '        stops = atr_stops((snap or {}).get("atr"), pip, atr_cfg) \\\n            if atr_cfg.enabled else None\n'
        '        base_sl, base_tp = stops if stops else (book.sl_pips, book.tp_pips)\n',
        '        atr_value = (snap or {}).get("atr")\n'
        '        if name == "swing" and atr_value:\n'
        '            side = setup["bias"]\n'
        '            entry_price = getattr(client.symbol_info_tick(settings.symbol),\n'
        '                                  "ask" if side is Signal.BUY else "bid", None)\n'
        '            pullback_low = (snap or {}).get("low")\n'
        '            pullback_high = (snap or {}).get("high")\n'
        '            buffer_price = 0.25 * atr_value\n'
        '            structural = ((pullback_low - buffer_price) if side is Signal.BUY\n'
        '                          else (pullback_high + buffer_price))\n'
        '            raw_distance = ((entry_price - structural) if side is Signal.BUY\n'
        '                            else (structural - entry_price))\n'
        '            capped_distance = min(raw_distance, 1.25 * atr_value)\n'
        '            base_sl = max(capped_distance / pip, 1.0)\n'
        '            base_tp = base_sl * 2.0\n'
        '        else:\n'
        '            stops = atr_stops(atr_value, pip, atr_cfg) if atr_cfg.enabled else None\n'
        '            base_sl, base_tp = stops if stops else (book.sl_pips, book.tp_pips)\n',
        "swing structural stop and 2R target",
    )

    text = replace_once(
        text,
        '    if settings.mode is not Mode.READ_ONLY and settings.trail_enabled:\n'
        '        _update_trailing_stops(client, settings)\n',
        '    if settings.mode is not Mode.READ_ONLY and settings.trail_enabled:\n'
        '        _update_trailing_stops(client, settings)\n',
        "leave generic trailing hook intact",
    )

    APP.write_text(text, encoding="utf-8")


def patch_trade_manager() -> None:
    text = TRADE_MANAGER.read_text(encoding="utf-8")
    marker = 'def trailing_sl('
    if 'def managed_swing_stop(' not in text:
        insert = '''def managed_swing_stop(is_buy: bool, entry: float, current: float,
                       current_sl: float, initial_risk: float) -> float | None:
    """Protect a swing trade without a tight fixed-pip trail.

    At 0.75R move to break-even. At 1R lock 0.25R. Never loosen the stop.
    """
    if initial_risk <= 0:
        return None
    direction = 1 if is_buy else -1
    progress = direction * (current - entry) / initial_risk
    if progress >= 1.0:
        candidate = entry + direction * (0.25 * initial_risk)
    elif progress >= 0.75:
        candidate = entry
    else:
        return None
    if current_sl:
        if is_buy and candidate <= current_sl:
            return None
        if not is_buy and candidate >= current_sl:
            return None
    return candidate


'''
        if marker not in text:
            raise RuntimeError("Could not find trailing_sl insertion point")
        text = text.replace(marker, insert + marker, 1)
    TRADE_MANAGER.write_text(text, encoding="utf-8")


def main() -> None:
    patch_app()
    patch_trade_manager()
    print("Swing exit patch complete. Run: python -m pytest -q")


if __name__ == "__main__":
    main()
