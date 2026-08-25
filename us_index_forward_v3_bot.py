"""Automatic demo forward-test runner for US_INDEX_FORWARD_V3.

Loads the frozen V3 artifact and its passed historical report. The signal feed can
remain US500 while execution is mapped to a verified broker index/futures symbol.
AUTO is hard-blocked unless MT5 explicitly reports a DEMO account.
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
from mt5_ai_bridge.us_index_forward_v3 import LOCKED_CONFIG, latest_decision, load_artifact

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "research" / "us_index_forward_v3_model.json"
RESULT_PATH = ROOT / "research" / "us_index_forward_v3_result.json"
STATE_PATH = ROOT / "state" / "us_index_forward_v3_state.json"
MAGIC = 20260826


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _managed_positions(client, symbol: str):
    return [p for p in (client.positions_get(symbol=symbol) or [])
            if int(getattr(p, "magic", 0) or 0) == MAGIC]


def _position_side(client, p) -> str:
    return "BUY" if int(p.type) == int(client.POSITION_TYPE_BUY) else "SELL"


def _bars_since(frame: pd.DataFrame, when: int) -> int:
    return int((frame["time"].astype(int) > int(when)).sum())


def _broker_lots(info, balance: float, price: float, stop_distance: float,
                 multiplier: float) -> float:
    """Size to V3's <=1% initial-stop ceiling, then apply broker/margin caps."""
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    if tick_size <= 0 or tick_value <= 0 or stop_distance <= 0:
        return 0.0

    risk_dollars = balance * (LOCKED_CONFIG.risk_percent / 100.0) * multiplier
    loss_per_lot = (stop_distance / tick_size) * tick_value
    raw = risk_dollars / loss_per_lot if loss_per_lot > 0 else 0.0

    margin_initial = float(getattr(info, "margin_initial", 0.0) or 0.0)
    if margin_initial > 0:
        margin_budget = balance * LOCKED_CONFIG.max_fraction_invested * multiplier
        raw = min(raw, margin_budget / margin_initial)
    elif _truthy("US_INDEX_FORWARD_V3_APPLY_NOTIONAL_CAP", "false"):
        contract = float(getattr(info, "trade_contract_size", 0.0) or 0.0)
        if contract > 0 and price > 0:
            raw = min(
                raw,
                balance * LOCKED_CONFIG.max_fraction_invested * multiplier / (price * contract),
            )

    minimum = float(getattr(info, "volume_min", 0.0) or 0.0) or 0.01
    maximum = float(getattr(info, "volume_max", 0.0) or 0.0) or raw
    step = float(getattr(info, "volume_step", 0.0) or 0.0) or minimum
    if raw < minimum:
        return 0.0
    return round(min(math.floor(raw / step + 1e-12) * step, maximum), 8)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    setup_logging()
    if not _truthy("US_INDEX_FORWARD_V3_ENABLED"):
        print("US_INDEX_FORWARD_V3_ENABLED is false. Nothing to do.")
        return 0
    if not MODEL_PATH.exists() or not RESULT_PATH.exists():
        print("Missing V3 model/result; run research/train_us_index_forward_v3.py first.")
        return 2

    artifact = load_artifact(MODEL_PATH)
    result = _load_json(RESULT_PATH)
    if not bool(result.get("forward_test_ready")):
        print("V3 growth/frequency gate did not pass; auto forward testing is blocked.")
        for reason in result.get("forward_test_blockers", []):
            print(f"  - {reason}")
        return 3

    settings = load_settings()
    if args.dry_run:
        settings = replace(settings, mode=Mode.READ_ONLY)

    signal_symbol = os.getenv(
        "US_INDEX_FORWARD_V3_SIGNAL_SYMBOL", LOCKED_CONFIG.research_symbol
    ).strip()
    execution_symbol = os.getenv(
        "US_INDEX_FORWARD_V3_EXEC_SYMBOL", signal_symbol
    ).strip()
    if not signal_symbol or not execution_symbol:
        print("V3 signal/execution symbols must be non-empty.")
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
            print("AUTO blocked: US_INDEX_FORWARD_V3 requires an explicit DEMO account.")
            return 4

        client.symbol_select(signal_symbol, True)
        client.symbol_select(execution_symbol, True)
        info = client.symbol_info(execution_symbol)
        if client.symbol_info(signal_symbol) is None or info is None:
            print("V3 signal or execution symbol unavailable.")
            return 2
        if settings.mode is not Mode.READ_ONLY and int(getattr(info, "trade_mode", 1)) == 0:
            print(f"Broker reports {execution_symbol} as trading-disabled.")
            return 4

        rates = client.copy_rates_from_pos(signal_symbol, LOCKED_CONFIG.timeframe, 1, 140)
        if rates is None or len(rates) < 90:
            print("Not enough completed D1 signal bars for V3.")
            return 2
        frame = pd.DataFrame(rates)
        decision = latest_decision(frame, artifact)
        print(
            f"{signal_symbol}->{execution_symbol} {decision.side}: "
            f"score={decision.score:.6f} threshold={decision.threshold:.6f}, "
            f"fast={decision.fast_prediction:.6f}, slow={decision.slow_prediction:.6f}"
        )

        state = _load_json(STATE_PATH)
        if not args.force and int(state.get("last_processed_bar", 0) or 0) == decision.time:
            print("Latest completed V3 signal bar already processed.")
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
        if total_dd >= 0.20 or daily_loss >= 0.02:
            print(f"V3 risk stop: total_dd={total_dd:.2%}, daily_loss={daily_loss:.2%}")
            _save_state(state)
            return 5

        governor = DrawdownGovernor(soft_limit=0.05, hard_limit=0.20, floor=0.25)
        governor.observe(peak)
        multiplier = governor.multiplier(equity)
        candidate = artifact.candidate

        positions = _managed_positions(client, execution_symbol)
        for p in list(positions):
            held = _bars_since(frame, int(state.get("entry_bar_time", 0) or 0))
            opposite = decision.side in {"BUY", "SELL"} and decision.side != _position_side(client, p)
            timed_out = held >= int(candidate["max_holding_bars"])
            if opposite or timed_out:
                reason = "opposite V3 signal" if opposite else "maximum V3 holding period"
                if settings.mode is Mode.READ_ONLY:
                    print(f"READ_ONLY: would close {p.ticket}: {reason}")
                else:
                    ok, message = close_position(client, int(p.ticket))
                    print(message)
                    if ok:
                        state.pop("entry_bar_time", None)
                positions = _managed_positions(client, execution_symbol)

        if decision.side == "FLAT" or positions:
            state["last_processed_bar"] = decision.time
            _save_state(state)
            return 0

        tick = client.symbol_info_tick(execution_symbol)
        if tick is None:
            print("No live execution tick for V3.")
            return 2
        execution_price = float(tick.ask if decision.side == "BUY" else tick.bid)

        # Scale the research proxy ATR into execution-price units when the signal
        # and execution symbols differ (e.g. US500 proxy -> verified futures symbol).
        atr_scale = execution_price / decision.close if decision.close > 0 else 1.0
        execution_atr = decision.atr * atr_scale
        stop_distance = float(candidate["stop_atr"]) * execution_atr
        lots = _broker_lots(info, balance, execution_price, stop_distance, multiplier)
        if lots <= 0:
            print("V3 risk-sized volume is below broker minimum/exposure cap; no trade.")
            state["last_processed_bar"] = decision.time
            _save_state(state)
            return 0

        pip = pip_size(client, execution_symbol)
        if pip is None or pip <= 0:
            print("Could not determine V3 execution-symbol pip/point size.")
            return 2
        sl_pips = stop_distance / pip
        tp_pips = float(candidate["take_profit_atr"]) * execution_atr / pip
        message = (
            f"{decision.side} {lots} {execution_symbol}; stop={sl_pips:.1f}, "
            f"target={tp_pips:.1f}, risk<={LOCKED_CONFIG.risk_percent:.2f}% "
            f"x governor {multiplier:.2f}"
        )

        if settings.mode is Mode.READ_ONLY:
            print(f"READ_ONLY: would place {message}")
        elif settings.mode is Mode.APPROVAL:
            if input(f"Place demo V3 {message}? Type YES: ") == "YES":
                ok, msg = place_market_order(
                    client, execution_symbol, decision.side, lots,
                    stop_loss_pips=sl_pips, take_profit_pips=tp_pips,
                    magic=MAGIC, comment="US_INDEX_FORWARD_V3",
                )
                print(msg)
                if ok:
                    state["entry_bar_time"] = decision.time
        else:
            ok, msg = place_market_order(
                client, execution_symbol, decision.side, lots,
                stop_loss_pips=sl_pips, take_profit_pips=tp_pips,
                magic=MAGIC, comment="US_INDEX_FORWARD_V3",
            )
            print(msg)
            if ok:
                state["entry_bar_time"] = decision.time

        state["last_processed_bar"] = decision.time
        state["signal_symbol"] = signal_symbol
        state["execution_symbol"] = execution_symbol
        _save_state(state)
        return 0
    finally:
        client.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
