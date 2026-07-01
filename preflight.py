"""Connection preflight check. Run:  python preflight.py

Connects to MT5 with your .env settings, prints account + symbol info, and
exits. It NEVER places a trade. Use this to confirm your setup before running
the live bridge.
"""

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client


def main() -> None:
    settings = load_settings()
    client = create_client()
    try:
        connect(client, settings)

        acct = client.account_info()
        if acct is None:
            print("Connected, but account_info() returned None.")
            return
        print(f"Account : login={acct.login}  balance={acct.balance}  "
              f"equity={acct.equity}")

        info = client.symbol_info(settings.symbol)
        tick = client.symbol_info_tick(settings.symbol)
        if info is None or tick is None:
            print(f"WARNING: symbol {settings.symbol!r} is not available. "
                  f"Add it to Market Watch in MT5.")
        else:
            print(f"Symbol  : {settings.symbol}  bid={tick.bid}  ask={tick.ask}  "
                  f"digits={info.digits}")

        print(f"Strategy: {settings.strategy}  Mode: {settings.mode.value}")
        print("Preflight OK -> you can now run:  python bridge.py")
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
