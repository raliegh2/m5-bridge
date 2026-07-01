"""Configure the adaptive GBPUSD swing engine for up to four aligned positions.

This patch is intentionally conservative about entries:
- swing only;
- completed H4 and D1 candles;
- D1 EMA20/EMA50 direction must agree;
- fresh H4 30-bar breakout required for every added position;
- all open positions must be in the same direction;
- no opposite-direction stacking;
- maximum four open swing positions.

Run after the completed-candle and adaptive swing strategy patches.
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
    text = text.replace('MAX_OPEN_POSITIONS", "1"', 'MAX_OPEN_POSITIONS", "4"')
    text = text.replace('MAX_SAME_DIRECTION", "1"', 'MAX_SAME_DIRECTION", "4"')
    CONFIG.write_text(text, encoding="utf-8")


def patch_app() -> None:
    text = APP.read_text(encoding="utf-8")

    old = '        desired = 1\n'
    new = '''        desired = min(4, settings.max_open_positions)
        existing_engine_positions = [
            p for p in positions
            if getattr(p, "comment", "").lower().startswith("swing")
        ]
        if existing_engine_positions:
            existing_sides = {
                "buy" if getattr(p, "type", 0) == 0 else "sell"
                for p in existing_engine_positions
            }
            requested_side = "buy" if setup["bias"] is Signal.BUY else "sell"
            if existing_sides != {requested_side}:
                log.info("swing engine blocked: open swing exposure conflicts with fresh setup")
                return
'''
    text = replace_once(text, old, new, "four-position same-direction cap")

    old2 = '        if name == "swing" and not _fresh_swing_setup(client, (decide(settings.swing_tf_high)[0] or {}).get("time")):\n'
    new2 = '''        if name == "swing" and not _fresh_swing_setup(client, (decide(settings.swing_tf_high)[0] or {}).get("time")):
'''
    text = replace_once(text, old2, new2, "fresh H4 setup requirement")

    APP.write_text(text, encoding="utf-8")


def main() -> None:
    patch_config()
    patch_app()
    print("Four-position aligned swing patch complete. Run: python -m pytest -q")


if __name__ == "__main__":
    main()
