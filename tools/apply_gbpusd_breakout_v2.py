"""Integrate the validated GBPUSD breakout v2 engine into app.py.

Run once from the repository root:
    python tools/apply_gbpusd_breakout_v2.py
    python -m pytest -q

Then set STRATEGY=gbpusd_breakout_v2 in .env.
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
        "from .trade_manager import close_position, modify_position_sl, trailing_sl\n",
        "from .trade_manager import close_position, modify_position_sl, trailing_sl\n"
        "from .gbpusd_breakout_v2 import run_breakout_cycle\n",
        "breakout v2 import",
    )

    old = '''    log.info("Risk: %s | day_loss=%.2f | active=%s", risk.message, day_loss, active)

    # Fast ENTRY read (TIMEFRAME = M15).
'''
    new = '''    log.info("Risk: %s | day_loss=%.2f | active=%s", risk.message, day_loss, active)

    # The validated GBPUSD engine owns its signal, entry, and H4 ATR trailing
    # path. Returning here prevents the legacy intraday/multi-book engines from
    # opening correlated or contradictory positions alongside it.
    if settings.strategy == "gbpusd_breakout_v2":
        thinking = run_breakout_cycle(
            client,
            journal,
            settings,
            account,
            risk_ok=risk.ok,
            active=active,
        )
        control = {"active": active} if state is not None else None
        _refresh_dashboard(
            client, journal, settings, control=control, thinking=thinking
        )
        _print_status(client, settings, active=active)
        return

    # Fast ENTRY read (TIMEFRAME = M15).
'''
    text = replace_once(text, old, new, "dedicated breakout execution path")

    APP.write_text(text, encoding="utf-8")
    print("GBPUSD breakout v2 integration complete.")
    print("Set STRATEGY=gbpusd_breakout_v2 and RISK_PERCENT=0.5 in .env")


if __name__ == "__main__":
    main()
