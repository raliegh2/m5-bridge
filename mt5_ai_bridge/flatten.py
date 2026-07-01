"""Close every open position, then exit. No trading, no loop.

Run:  python -m mt5_ai_bridge.flatten            (closes ALL symbols)
      python -m mt5_ai_bridge.flatten GBPUSD     (closes only that symbol)

Or double-click "Close All Trades.bat". Stop the bot first if you want it to
stay flat (otherwise the bot may open new trades on its next loop).
"""

import sys
from typing import Optional

from .app import connect
from .config import load_settings
from .logging_config import get_logger, setup_logging
from .mt5_client import create_client
from .trade_manager import close_all_positions

log = get_logger("flatten")


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    symbol = argv[0] if argv else None

    settings = load_settings()
    setup_logging(settings.log_level)

    try:
        client = create_client()
        connect(client, settings)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect to MT5: {exc}")
        print("Make sure MetaTrader 5 is installed, open, and logged in, then "
              "try again.")
        return 1

    try:
        results = close_all_positions(client, symbol)
        if not results:
            print("No open positions to close." if not symbol
                  else f"No open {symbol} positions to close.")
            return 0

        ok = 0
        for ticket, success, message in results:
            print(f"  {'OK ' if success else 'ERR'}  ticket {ticket}: {message}")
            ok += 1 if success else 0
        print(f"\nClosed {ok}/{len(results)} position(s).")
        return 0 if ok == len(results) else 2
    finally:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
