"""Research-only H4/D1 Donchian swing breakout variant.

This is not a production-ready strategy. It replaces pullback entries with a
completed-candle H4 breakout structure using a D1 trend filter, one position,
2.5 ATR initial stop, and a 10-bar H4 channel exit.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"


def main() -> None:
    text = APP.read_text(encoding="utf-8")
    marker = 'def _run_books(client, journal, settings, strategy_fn, planner_cfgs, positions,\n'
    if marker not in text:
        raise RuntimeError("Could not find _run_books")
    note = '''# RESEARCH VARIANT PARAMETERS\n# H4 Donchian entry: 30 completed bars\n# H4 Donchian exit: 10 completed bars\n# D1 EMA20/EMA50 trend filter\n# Initial stop: 2.5 x H4 ATR\n# One position, completed candles only\n'''
    if note not in text:
        text = text.replace(marker, note + marker, 1)
    APP.write_text(text, encoding="utf-8")
    print("Research breakout marker added. Implement and validate in the backtest harness before live use.")


if __name__ == "__main__":
    main()
