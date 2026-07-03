"""Safe preflight for the final V12 demo profile.

Run with:  python v12_final_preflight.py

Connects to MT5, verifies a demo account, checks all five symbols and broker pip
values, confirms there are no unregistered positions, and exits without sending
an order.
"""
from __future__ import annotations

import os

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.execution import pip_size
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.v12_final_risk import ALLOWED_SYMBOLS, PROFILE_ID, validate_profile
from mt5_ai_bridge.v12_final_state import StateStore


def _is_demo(client, account) -> bool:
    constant = getattr(client, "ACCOUNT_TRADE_MODE_DEMO", None)
    mode = getattr(account, "trade_mode", None)
    if constant is not None and mode is not None:
        return mode == constant
    return "demo" in str(getattr(account, "server", "")).lower()


def main() -> None:
    validate_profile()
    settings = load_settings()
    client = create_client()
    state = StateStore(os.getenv("V12_FINAL_STATE_PATH", "v12_final_demo_state.json"))
    errors = []
    try:
        connect(client, settings)
        account = client.account_info()
        if account is None:
            raise RuntimeError("account_info() returned None")
        if not _is_demo(client, account):
            errors.append("Connected account is not confirmed as DEMO.")

        positions = list(client.positions_get() or [])
        registered = {item.ticket for item in state.state.positions.values()}
        current = {int(item.ticket) for item in positions}
        unknown = current - registered
        if unknown:
            errors.append(f"Unregistered/manual open positions: {sorted(unknown)}")

        for symbol in sorted(ALLOWED_SYMBOLS):
            info = client.symbol_info(symbol)
            tick = client.symbol_info_tick(symbol)
            pip = pip_size(client, symbol)
            if info is None or tick is None or pip is None:
                errors.append(f"{symbol}: missing symbol/tick/pip data; add it to Market Watch.")
                continue
            calculator = getattr(client, "order_calc_profit", None)
            if calculator is None:
                errors.append(f"{symbol}: broker pip-value calculation is unavailable.")
                continue
            one_pip = calculator(client.ORDER_TYPE_BUY, symbol, 1.0, tick.ask, tick.ask + pip)
            if one_pip is None or abs(float(one_pip)) <= 0:
                errors.append(f"{symbol}: invalid one-lot pip value.")
            else:
                spread = (tick.ask - tick.bid) / pip
                print(f"{symbol}: bid={tick.bid} ask={tick.ask} spread={spread:.2f}p "
                      f"pip_value={abs(float(one_pip)):.4f}")

        print(f"Profile : {PROFILE_ID}")
        print(f"Account : login={account.login} balance={account.balance} equity={account.equity}")
        if errors:
            print("\nPREFLIGHT FAILED")
            for error in errors:
                print(f"- {error}")
            raise SystemExit(1)
        print("\nPREFLIGHT PASSED: risk profile and broker inputs are ready.")
        print("No order was sent.")
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
