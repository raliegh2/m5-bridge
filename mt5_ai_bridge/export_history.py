"""Export historical bars from MT5 to a CSV the backtester can read.

Run:  python -m mt5_ai_bridge.export_history                 (SYMBOL from .env, M5)
      python -m mt5_ai_bridge.export_history GBPUSD --timeframe M5 --bars 50000

Or double-click "Export History.bat". MetaTrader 5 must be open and logged in.
The CSV (time/open/high/low/close) drops straight into:
    python -m mt5_ai_bridge.backtest_books <that file>
"""

import argparse
import sys
from typing import Optional

import pandas as pd

from .app import connect
from .config import load_settings
from .logging_config import get_logger, setup_logging
from .mt5_client import create_client

log = get_logger("export")


def export_history(client, symbol: str, timeframe: str, bars: int,
                   out_path: str) -> int:
    """Pull bars from the client and write an OHLC CSV. Returns the row count."""
    rates = client.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None or len(rates) == 0:
        return 0
    df = pd.DataFrame(rates)[["time", "open", "high", "low", "close"]]
    df.to_csv(out_path, index=False)
    return len(df)


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        prog="mt5_ai_bridge.export_history",
        description="Export MT5 history to a CSV for backtesting.")
    p.add_argument("symbol", nargs="?", default=None, help="Defaults to .env SYMBOL")
    p.add_argument("--timeframe", default="M5")
    p.add_argument("--bars", type=int, default=50_000)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    settings = load_settings()
    setup_logging(settings.log_level)
    symbol = args.symbol or settings.symbol
    out = args.out or f"{symbol}_{args.timeframe}.csv"

    try:
        client = create_client()
        connect(client, settings)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect to MT5: {exc}")
        print("Open MetaTrader 5 and log in (and check the symbol), then retry.")
        return 1

    try:
        count = export_history(client, symbol, args.timeframe, args.bars, out)
        if count == 0:
            print(f"No history returned for {symbol} {args.timeframe}. "
                  f"Make sure the symbol is in Market Watch.")
            return 2
        print(f"Wrote {count} {args.timeframe} bars to {out}")
        print(f"Backtest it:  python -m mt5_ai_bridge.backtest_books {out}")
        return 0
    finally:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
