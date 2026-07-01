"""Patch mt5_ai_bridge/app.py to route STRATEGY=gbpusd_v4 to the V4 engine."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
if not (ROOT / "mt5_ai_bridge").exists():
    ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Patch target not found: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = APP.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from .trade_manager import close_position, modify_position_sl, trailing_sl\n",
        "from .trade_manager import close_position, modify_position_sl, trailing_sl\n"
        "from .gbpusd_v4 import run_v4_cycle\n",
        "V4 import",
    )
    old = '''    log.info("Risk: %s | day_loss=%.2f | active=%s", risk.message, day_loss, active)

    # Fast ENTRY read (TIMEFRAME = M15).
'''
    new = '''    log.info("Risk: %s | day_loss=%.2f | active=%s", risk.message, day_loss, active)

    if settings.strategy == "gbpusd_v4":
        thinking = run_v4_cycle(
            client, journal, settings, account, risk_ok=risk.ok, active=active
        )
        control = {"active": active} if state is not None else None
        _refresh_dashboard(
            client, journal, settings, control=control, thinking=thinking
        )
        _print_status(client, settings, active=active)
        return

    # Fast ENTRY read (TIMEFRAME = M15).
'''
    text = replace_once(text, old, new, "V4 dedicated execution path")
    APP.write_text(text, encoding="utf-8")
    print("V4 app integration applied.")


if __name__ == "__main__":
    main()
