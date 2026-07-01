"""Wire configurable exit-management .env values into the bot.

Run once from the repository root:
    python tools/apply_exit_config_patch.py

The script is idempotent: running it again after a successful patch makes no
additional changes.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "mt5_ai_bridge" / "config.py"
APP = ROOT / "mt5_ai_bridge" / "app.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Could not find patch target: {label}")
    return text.replace(old, new, 1)


def patch_config() -> None:
    text = CONFIG.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "    trail_distance_pips: float\n",
        "    trail_distance_pips: float\n"
        "    break_even_trigger: float\n"
        "    break_even_buffer_pips: float\n"
        "    partial_profit_trigger: float\n"
        "    partial_profit_lock_ratio: float\n"
        "    trailing_start: float\n"
        "    trailing_distance_atr: float\n",
        "Settings exit fields",
    )

    text = replace_once(
        text,
        '        trail_distance_pips=_get_float("TRAIL_DISTANCE_PIPS", 15),\n',
        '        trail_distance_pips=_get_float("TRAIL_DISTANCE_PIPS", 15),\n'
        '        break_even_trigger=_get_float("BREAK_EVEN_TRIGGER", 1.0),\n'
        '        break_even_buffer_pips=_get_float("BREAK_EVEN_BUFFER_PIPS", 0.5),\n'
        '        partial_profit_trigger=_get_float("PARTIAL_PROFIT_TRIGGER", 0.75),\n'
        '        partial_profit_lock_ratio=_get_float("PARTIAL_PROFIT_LOCK_RATIO", 0.35),\n'
        '        trailing_start=_get_float("TRAILING_START", 0.85),\n'
        '        trailing_distance_atr=_get_float("TRAILING_DISTANCE_ATR", 1.50),\n',
        "load exit values",
    )

    CONFIG.write_text(text, encoding="utf-8")
    print(f"Patched {CONFIG.relative_to(ROOT)}")


def patch_app() -> None:
    text = APP.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from .trade_manager import close_position, modify_position_sl, trailing_sl\n",
        "from .trade_manager import (close_position, managed_stop_loss,\n"
        "                            modify_position_sl)\n",
        "trade manager import",
    )

    old = '''        new_sl = trailing_sl(is_buy, getattr(p, "price_open", 0.0),
                             getattr(p, "price_current", 0.0), p.sl, pip,
                             settings.trail_start_pips, settings.trail_distance_pips)
        if new_sl is None:
            continue
        ok, message = modify_position_sl(client, p, new_sl)
        (log.info if ok else log.warning)("Trail: %s", message)
'''

    new = '''        entry = getattr(p, "price_open", 0.0)
        current = getattr(p, "price_current", 0.0)
        take_profit = getattr(p, "tp", 0.0) or None
        risk_pips = abs(entry - (getattr(p, "sl", 0.0) or entry)) / pip
        tp_distance_pips = abs((take_profit or entry) - entry) / pip

        # TRAILING_START is expressed as a fraction of the planned TP path.
        # Fall back to the classic pip threshold when no TP is available.
        dynamic_trail_start = (
            tp_distance_pips * settings.trailing_start
            if tp_distance_pips > 0 else settings.trail_start_pips
        )

        # ATR-distance configuration is converted into pips using the original
        # risk distance as a conservative volatility proxy for open positions.
        dynamic_trail_distance = max(
            settings.trail_distance_pips,
            risk_pips * settings.trailing_distance_atr,
        )

        decision = managed_stop_loss(
            is_buy=is_buy,
            entry=entry,
            current=current,
            current_sl=getattr(p, "sl", 0.0),
            pip=pip,
            trail_start_pips=dynamic_trail_start,
            trail_distance_pips=dynamic_trail_distance,
            take_profit=take_profit,
            break_even_at_r=settings.break_even_trigger,
            break_even_buffer_pips=settings.break_even_buffer_pips,
            tp_lock_ratio=settings.partial_profit_trigger,
            tp_lock_profit_ratio=settings.partial_profit_lock_ratio,
        )
        if decision.new_sl is None:
            continue
        ok, message = modify_position_sl(client, p, decision.new_sl)
        log_fn = log.info if ok else log.warning
        log_fn("Protection [%s]: %s", decision.reason, message)
'''

    text = replace_once(text, old, new, "configured protective stop loop")
    APP.write_text(text, encoding="utf-8")
    print(f"Patched {APP.relative_to(ROOT)}")


def main() -> None:
    patch_config()
    patch_app()
    print("Exit configuration wiring complete. Run: python -m pytest -q")


if __name__ == "__main__":
    main()
