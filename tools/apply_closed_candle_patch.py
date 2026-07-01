"""Force live signals and backtests to use only fully closed candles.

Run once from the repository root:
    python tools/apply_closed_candle_patch.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDICATORS = ROOT / "mt5_ai_bridge" / "indicators.py"
BACKTEST = ROOT / "mt5_ai_bridge" / "backtest_books.py"


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
        'def get_rates(client, symbol: str, timeframe: str = "M30", bars: int = 200):\n'
        '    rates = client.copy_rates_from_pos(symbol, timeframe, 0, bars)\n',
        'def get_rates(client, symbol: str, timeframe: str = "M30", bars: int = 200,\n'
        '              closed_only: bool = True):\n'
        '    """Read completed candles by default; MT5 position 0 is still forming."""\n'
        '    start_pos = 1 if closed_only else 0\n'
        '    rates = client.copy_rates_from_pos(symbol, timeframe, start_pos, bars)\n',
        "closed candle rate loading",
    )
    INDICATORS.write_text(text, encoding="utf-8")
    print("Patched mt5_ai_bridge/indicators.py")


def patch_backtest() -> None:
    text = BACKTEST.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '        sub = r[r["time"] <= now_t].tail(count)\n',
        '        available = r[r["time"] <= now_t]\n'
        '        if start > 0:\n'
        '            available = available.iloc[:-start] if len(available) > start else available.iloc[0:0]\n'
        '        sub = available.tail(count)\n',
        "backtester start-position handling",
    )
    BACKTEST.write_text(text, encoding="utf-8")
    print("Patched mt5_ai_bridge/backtest_books.py")


def main() -> None:
    patch_indicators()
    patch_backtest()
    print("Closed-candle patch complete. Run: python -m pytest -q")


if __name__ == "__main__":
    main()
