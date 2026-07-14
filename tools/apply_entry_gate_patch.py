"""Add fresh-setup entry gating to the live dual-engine loop.

Run once from the repository root:
    python tools/apply_entry_gate_patch.py

The patch prevents repeated orders while the same setup remains continuously
valid. A setup must reset to invalid before it can trigger again.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Could not find patch target: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = APP.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'INTRADAY_MICRO_TIMEFRAMES = ("M1", "M5")\n',
        'INTRADAY_MICRO_TIMEFRAMES = ("M1", "M5")\n\n'
        '# Per-process entry latch. Keyed by client identity and engine name so\n'
        '# repeated valid signals do not create repeated orders every loop.\n'
        '_ENTRY_GATES = {}\n\n\n'
        'def _fresh_setup(client, engine: str, setup: dict, candle_time) -> bool:\n'
        '    """Allow one entry per continuous valid setup.\n\n'
        '    The latch resets only after the setup becomes invalid. The candle\n'
        '    timestamp is stored for audit/debugging and prevents duplicate\n'
        '    processing of the same ready candle.\n'
        '    """\n'
        '    key = (id(client), engine)\n'
        '    if not setup.get("valid"):\n'
        '        _ENTRY_GATES.pop(key, None)\n'
        '        return False\n'
        '    signature = (getattr(setup.get("bias"), "value", None), str(candle_time))\n'
        '    if key in _ENTRY_GATES:\n'
        '        return False\n'
        '    _ENTRY_GATES[key] = signature\n'
        '    return True\n',
        "entry gate helper",
    )

    text = replace_once(
        text,
        '    def open_engine(name, setup, book, snap_tf, risk_percent):\n'
        '        nonlocal total_open\n'
        '        if not setup["valid"]:\n'
        '            log.info("%s engine waiting: %s", name, setup["reason"])\n'
        '            return\n',
        '    def open_engine(name, setup, book, snap_tf, risk_percent, candle_time):\n'
        '        nonlocal total_open\n'
        '        if not setup["valid"]:\n'
        '            _fresh_setup(client, name, setup, candle_time)\n'
        '            log.info("%s engine waiting: %s", name, setup["reason"])\n'
        '            return\n'
        '        if not _fresh_setup(client, name, setup, candle_time):\n'
        '            log.info("%s engine waiting: setup already consumed; waiting for reset.", name)\n'
        '            return\n',
        "open engine gate",
    )

    text = replace_once(
        text,
        '    open_engine("swing", state["swing"], swing_book,\n'
        '                settings.swing_tf_high, settings.swing_risk_percent)\n'
        '    open_engine("intraday", state["intraday"], intraday_book,\n'
        '                settings.timeframe, settings.intraday_risk_percent)\n',
        '    swing_candle = (decide(settings.swing_tf_high)[0] or {}).get("time")\n'
        '    intraday_candle = (decide(settings.timeframe)[0] or {}).get("time")\n'
        '    open_engine("swing", state["swing"], swing_book,\n'
        '                settings.swing_tf_high, settings.swing_risk_percent,\n'
        '                swing_candle)\n'
        '    open_engine("intraday", state["intraday"], intraday_book,\n'
        '                settings.timeframe, settings.intraday_risk_percent,\n'
        '                intraday_candle)\n',
        "engine candle wiring",
    )

    APP.write_text(text, encoding="utf-8")
    print("Patched mt5_ai_bridge/app.py with fresh-setup entry gating.")
    print("Run: python -m pytest -q")


if __name__ == "__main__":
    main()
