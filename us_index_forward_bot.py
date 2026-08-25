"""Automatic demo forward-test runner for US_INDEX_FORWARD_V1.

Run once per day after the target market has a completed D1 bar. The runner
uses a frozen trained artifact and refuses to auto-trade unless the historical
post-training gate passed. AUTO is hard-blocked on non-demo accounts.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.enums import Mode
from mt5_ai_bridge.execution import pip_size, place_market_order
from mt5_ai_bridge.logging_config import setup_logging
from mt5_ai_bridge.mt5_client import RealMT5Client
from mt5_ai_bridge.risk_v18 import DrawdownGovernor
from mt5_ai_bridge.trade_manager import close_position
from mt5_ai_bridge.us_index_forward_v1 import (
    LOCKED_CONFIG,
    latest_decision,
    load_artifact,
)

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "research" / "us_index_forward_v1_model.json"
RESULT_PATH = ROOT / "research" / "us_index_forward_v1_result.json"
STATE_PATH = ROOT / "state" / "us_index_forward_v1_state.json"
MAGIC = 20260824


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _load_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return {} if default is None else dict(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _managed_positions(client, symbol: str):
    return [
        p for p in (client.positions_get(symbol=symbol) or [])
        if int(getattr(p, "magic", 0) or 0) == MAGIC
    ]


def _side_of_position(client, position) -> str:
    return "BUY" if int(position.type) == int(client.POSITION_TYPE_BUY) else "SELL"


def _bars_since(frame: pd.DataFrame, when: int) -> int:
    return int((frame["time"].astype(int) > int(when)).sum())


def _broker_lots(info, balance: float, price: float, stop_distance: float,
                 multiplier: float) -> float:
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    if tick_size <= 0 or tick_value <= 0 or stop_distance <= 0:
        return 0.0

    risk_dollars = balance * (LOCKED_CONFIG.risk_percent / 100.0) * multiplier
    loss_per_lot = (stop_distance / tick_size) * tick_value
    if loss_per_lot <= 0:
        return 0.0
    raw = risk_dollars / loss_per_lot

    if _truthy("US_INDEX_FORWARD_APPLY_NOTIONAL_CAP", "true"):
        contract = float(getattr(info, "trade_contract_size", 0.0) or 0.0)
        if contract > 0 and price > 0:
            notional_per_lot = price * contract
            raw = min(
                raw,
                balance * LOCKED_CONFIG.max_fraction_invested * multiplier
                / notional_per_lot,
            )

    minimum = float(getattr(info, "volume_min", 0.0) or 0.0) or 0.01
    maximum = float(getattr(info, "volume_max", 0.0) or 0.0) or raw
    step = float(getattr(info, "volume_step", 0.0) or 0.0) or minimum
    if raw < minimum:
        return 0.0
    steps = math.floor(raw / step + 1e-12)
    return round(min(steps * step, maximum), 8)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="allow re-processing the latest completed D1 bar")
    args = parser.parse_args(argv)

    setup_logging()
    if not _truthy("US_INDEX_FORWARD_ENABLED"):
        print("US_INDEX_FORWARD_ENABLED is false. Nothing to do.")
        return 0
    if not MODEL_PATH.exists() or not RESULT_PATH.exists():
        print("Missing trained artifact/result. Run research/train_us_index_forward_v1.py first.")
        return 2

    artifact = load_artifact(MODEL_PATH)
    result = _load_json(RESULT_PATH)
    if not bool(result.get("forward_test_ready")):
        print("Historical gate did not pass; automatic forward testing is blocked.")
        for reason in result.get("forward_test_blockers", []):
            print(f"  - {reason}")
        return 3

    settings = load_settings()
    if args.dry_run:
        settings = replace(settings, mode=Mode.READ_ONLY)
    symbol = os.getenv("US_INDEX_FORWARD_EXEC_SYMBOL", LOCKED_CONFIG.research_symbol).strip()
    if not symbol:
        print("US_INDEX_FORWARD_EXEC_SYMBOL is empty.")
        return 2

    client = RealMT5Client()
    if not client.initialize():
        print(f"MT5 initialize failed: {client.last_error()}")
        return 1
    try:
        if settings.has_credentials:
            client.login(settings.login, settings.password, settings.server)
        account = client.account_info()
        if account is None:
            print("No account info.")
            return 1

        if settings.mode is Mode.AUTO and int(getattr(account, "trade_mode", -1)) != 0:
            print("AUTO blocked: US_INDEX_FORWARD_V1 requires an explicit DEMO account.")
            return 4

        client.symbol_select(symbol, True)
        info = client.symbol_info(symbol)
        if info is None:
            print(f"Broker symbol unavailable: {symbol}")
            return 2
        if settings.mode is not Mode.READ_ONLY and int(getattr(info, "trade_mode", 1)) == 0:
            print(f"Broker reports {symbol} as trading-disabled.")
            return 4

        need = max(90, LOCKED_CONFIG.lookback + LOCKED_CONFIG.atr_period + 30)
        rates = client.copy_rates_from_pos(symbol, LOCKED_CONFIG.timeframe, 1, need)
        if rates is None or len(rates) < LOCKED_CONFIG.lookback + 25:
            print(f"Not enough completed {LOCKED_CONFIG.timeframe} bars for {symbol}.")
            return 2
        frame = pd.DataFrame(rates)
        decision = latest_decision(frame, artifact)
        print(
            f"{symbol} {decision.side}: prediction={decision.prediction:.6f}, "
            f"threshold={decision.threshold:.6f}, ATR={decision.atr:.2f}, "
            f"close={decision.close:.2f}"
        )

        state = _load_json(STATE_PATH)
        if not args.force and int(state.get("last_processed_bar", 0) or 0) == decision.time:
            print("Latest completed bar already processed.")
            return 0

        equity = float(getattr(account, "equity", 0.0) or 0.0)
        balance = float(getattr(account, "balance", equity) or equity)
        peak = max(float(state.get("peak_equity", 0.0) or 0.0), equity)
        state["peak_equity"] = peak
        today = datetime.now(timezone.utc).date().isoformat()
        if state.get("day") != today:
            state["day"] = today
            state["day_start_equity"] = equity
        day_start = float(state.get("day_start_equity", equity) or equity)

        total_dd = (peak - equity) / peak if peak > 0 else 0.0
        daily_loss = (day_start - equity) / day_start if day_start > 0 else 0.0
        if total_dd >= 0.20:
            print(f"Risk stop: total drawdown {total_dd:.2%} >= 20%.")
            _save_state(state)
            return 5
        if daily_loss >= 0.02:
            print(f"Risk stop: daily loss {daily_loss:.2%} >= 2%.")
            _save_state(state)
            return 5

        governor = DrawdownGovernor(soft_limit=0.05, hard_limit=0.20, floor=0.25)
        governor.observe(peak)
        multiplier = governor.multiplier(equity)

        positions = _managed_positions(client, symbol)
        for position in list(positions):
            entry_bar_time = int(state.get("entry_bar_time", 0) or 0)
            held_bars = _bars_since(frame, entry_bar_time) if entry_bar_time else 0
            position_side = _side_of_position(client, position)
            opposite = decision.side in {"BUY", "SELL"} and decision.side != position_side
            timed_out = held_bars >= LOCKED_CONFIG.max_holding_bars
            if opposite or timed_out:
                why = "opposite model signal" if opposite else "maximum holding period"
                if settings.mode is Mode.READ_ONLY:
                    print(f"READ_ONLY: would close ticket {position.ticket}: {why}")
                else:
                    ok, message = close_position(client, int(position.ticket))
                    print(message)
                    if ok:
                        state.pop("entry_bar_time", None)
                positions = _managed_positions(client, symbol)

        if decision.side == "FLAT" or positions:
            state["last_processed_bar"] = decision.time
            _save_state(state)
            return 0

        stop_distance = LOCKED_CONFIG.stop_atr * decision.atr
        lots = _broker_lots(info, balance, decision.close, stop_distance, multiplier)
        if lots <= 0:
            print("Risk-sized volume is below the broker minimum; no trade.")
            state["last_processed_bar"] = decision.time
            _save_state(state)
            return 0

        pip = pip_size(client, symbol)
        if pip is None or pip <= 0:
            print("Could not determine broker pip/point size.")
            return 2
        sl_pips = stop_distance / pip
        tp_pips = (LOCKED_CONFIG.take_profit_atr * decision.atr) / pip
        message = (
            f"{decision.side} {lots} {symbol}, stop={sl_pips:.1f} broker-pips, "
            f"target={tp_pips:.1f}, risk={LOCKED_CONFIG.risk_percent:.2f}% "
            f"x governor {multiplier:.2f}"
        )

        if settings.mode is Mode.READ_ONLY:
            print(f"READ_ONLY: would place {message}")
        elif settings.mode is Mode.APPROVAL:
            if input(f"Place demo {message}? Type YES: ") == "YES":
                ok, order_message = place_market_order(
                    client, symbol, decision.side, lots,
                    stop_loss_pips=sl_pips, take_profit_pips=tp_pips,
                    magic=MAGIC, comment="US_INDEX_FORWARD_V1",
                )
                print(order_message)
                if ok:
                    state["entry_bar_time"] = decision.time
        else:
            ok, order_message = place_market_order(
                client, symbol, decision.side, lots,
                stop_loss_pips=sl_pips, take_profit_pips=tp_pips,
                magic=MAGIC, comment="US_INDEX_FORWARD_V1",
            )
            print(order_message)
            if ok:
                state["entry_bar_time"] = decision.time

        state["last_processed_bar"] = decision.time
        _save_state(state)
        return 0
    finally:
        client.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
