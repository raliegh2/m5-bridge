"""Continuous named-engine runner for the final V12 $3,201.58 model.

The runner reads H1/H4/D1 bars from the connected MT5 terminal, rebuilds the
same frozen indicators and candidate families used by the final research model,
applies the final portfolio gate through ``FinalV12Adapter``, and writes every
validated proposal to JSONL.

It does not call the broker order API. Use ``--once`` for one scan or leave it
running to scan each completed H1/H4 candle.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

import v12_plus_validated_assets_backtest as study  # noqa: E402
import v13_expanded_assets_backtest as base  # noqa: E402

from mt5_ai_bridge.app import connect  # noqa: E402
from mt5_ai_bridge.config import load_settings  # noqa: E402
from mt5_ai_bridge.execution import pip_size  # noqa: E402
from mt5_ai_bridge.mt5_client import create_client  # noqa: E402
from mt5_ai_bridge.v12_final_adapter import FinalV12Adapter, NamedEngineSignal  # noqa: E402

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
AUDUSD_PARAMS = study.AUDUSDParams(15.0, 0.30, 0.25)
STATE_FILE = ROOT / "v12_final_runner_state.json"
PROPOSAL_LOG = ROOT / "v12_final_proposals.jsonl"

EXIT_MAP = {
    ("GBPUSD_V10_PRECISION", "PRIMARY_16UTC_BREAKOUT"): (1.50, 3.0),
    ("GBPUSD_V10_PRECISION", "SECONDARY_12UTC_BREAKOUT"): (1.50, 3.0),
    ("GBPUSD_V10_PRECISION", "GBPUSD_SWING_V5_PULLBACK_ADDON"): (1.25, 2.50),
    ("GBPUSD_SWING_RETEST", "H4_BREAKOUT_RETEST"): (1.50, 4.0),
    ("EURUSD_SWING_CORE", "H4_DONCHIAN_BREAKOUT"): (1.25, 3.0),
    ("EURUSD_SWING_RETEST", "H1_BREAKOUT_RETEST"): (1.25, 3.0),
    ("GBPJPY_SWING_CORE", "H4_DONCHIAN_BREAKOUT"): (1.25, 3.0),
    ("AUDUSD_TREND_PULLBACK", "D1_H4_EMA_PULLBACK_04_08UTC"): (1.25, 2.0),
    ("USDJPY_SAFE_HAVEN_BREAKOUT", "D1_H4_40BAR_BREAKOUT"): (1.50, 3.0),
}


def _frame(rates) -> pd.DataFrame:
    frame = pd.DataFrame(rates)
    if frame.empty:
        return frame
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    required = ["time", "open", "high", "low", "close", "tick_volume"]
    return frame[required].dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)


def prepare_live_frames(client, symbol: str,
                        h1_count: int = 3000,
                        h4_count: int = 2500,
                        d1_count: int = 800) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    h1 = _frame(client.copy_rates_from_pos(symbol, "H1", 1, h1_count))
    h4 = _frame(client.copy_rates_from_pos(symbol, "H4", 1, h4_count))
    d1 = _frame(client.copy_rates_from_pos(symbol, "D1", 1, d1_count))
    if min(len(h1), len(h4), len(d1)) < 100:
        raise RuntimeError(f"{symbol}: insufficient closed-bar history")

    h4["atr14"] = base.atr(h4)
    h4["ema20"] = base.ema(h4["close"], 20)
    h4["ema50"] = base.ema(h4["close"], 50)
    h4["avg_volume"] = h4["tick_volume"].rolling(20, min_periods=20).mean()
    h4["volume_ratio"] = h4["tick_volume"] / h4["avg_volume"]
    h4["atr_ratio"] = h4["atr14"] / h4["atr14"].rolling(20, min_periods=20).mean()
    h4["range"] = h4["high"] - h4["low"]
    h4["body_ratio"] = (h4["close"] - h4["open"]).abs() / h4["range"].replace(0, np.nan)
    h4["close_location"] = (h4["close"] - h4["low"]) / h4["range"].replace(0, np.nan)
    h4["plus_di"], h4["minus_di"], h4["adx14"] = base.directional(h4)
    h4["end"] = h4["time"] + pd.Timedelta(hours=4)

    h1["atr14"] = base.atr(h1)
    h1["ema20"] = base.ema(h1["close"], 20)
    h1["ema50"] = base.ema(h1["close"], 50)
    h1["end"] = h1["time"] + pd.Timedelta(hours=1)
    h1["ema_sep_atr"] = (h1["ema20"] - h1["ema50"]).abs() / h1["atr14"]
    h1["hour"] = h1["end"].dt.hour

    d1["ema20"] = base.ema(d1["close"], 20)
    d1["ema50"] = base.ema(d1["close"], 50)
    d1["available"] = d1["time"] + pd.Timedelta(days=1)
    daily = d1[["available", "close", "ema20", "ema50"]].rename(columns={
        "close": "dclose", "ema20": "dema20", "ema50": "dema50"
    })
    h4 = pd.merge_asof(
        h4.sort_values("time"), daily.sort_values("available"),
        left_on="time", right_on="available", direction="backward",
    )

    d1["daily_atr14"] = base.atr(d1)
    d1["daily_ema20"] = base.ema(d1["close"], 20)
    d1["daily_ema50"] = base.ema(d1["close"], 50)
    d1["daily_ema20_slope"] = d1["daily_ema20"].diff(5) / 5
    d1["available_v12"] = d1["time"] + pd.Timedelta(days=1)
    h4 = pd.merge_asof(
        h4.sort_values("time"),
        d1[["available_v12", "daily_atr14", "daily_ema20_slope"]].sort_values("available_v12"),
        left_on="time", right_on="available_v12", direction="backward",
    )
    h4["ema_sep_atr"] = (h4["ema20"] - h4["ema50"]).abs() / h4["atr14"]
    h4["atr_pct_252"] = h4["atr14"].rolling(252, min_periods=100).rank(pct=True)
    h4["prior_high"] = h4["high"].rolling(55, min_periods=55).max().shift(1)
    h4["prior_low"] = h4["low"].rolling(55, min_periods=55).min().shift(1)
    h4["directional_di_gap_long"] = h4["plus_di"] - h4["minus_di"]
    h4["directional_di_gap_short"] = h4["minus_di"] - h4["plus_di"]
    long = (
        (h4["dclose"] > h4["dema20"]) & (h4["dema20"] > h4["dema50"])
        & (h4["close"] > h4["ema20"]) & (h4["adx14"] >= 20)
        & (h4["close"] > h4["prior_high"])
    )
    short = (
        (h4["dclose"] < h4["dema20"]) & (h4["dema20"] < h4["dema50"])
        & (h4["close"] < h4["ema20"]) & (h4["adx14"] >= 20)
        & (h4["close"] < h4["prior_low"])
    )
    h4["breakout_side"] = np.where(long, 1, np.where(short, -1, 0))
    h4["breakout_level"] = np.where(
        h4["breakout_side"] > 0, h4["prior_high"],
        np.where(h4["breakout_side"] < 0, h4["prior_low"], np.nan),
    )
    h4["directional_di_gap"] = np.where(
        h4["breakout_side"] > 0, h4["directional_di_gap_long"], h4["directional_di_gap_short"]
    )
    h4["daily_slope_dir"] = np.where(
        h4["breakout_side"] > 0,
        h4["daily_ema20_slope"] / h4["daily_atr14"],
        -h4["daily_ema20_slope"] / h4["daily_atr14"],
    )
    return h1, h4, d1


def build_final_candidates(prepared: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    gbp_h1, gbp_h4, _ = prepared["GBPUSD"]
    frames.extend([
        study._gbpusd_precision(gbp_h4),
        study._gbpusd_retest_candidates(gbp_h4),
    ])
    eur_h1, eur_h4, _ = prepared["EURUSD"]
    frames.extend([
        study._v12_core_candidates("EURUSD", eur_h4),
        study._h1_retest_candidates("EURUSD", eur_h1, eur_h4),
    ])
    _, gbpjpy_h4, _ = prepared["GBPJPY"]
    frames.append(study._v12_core_candidates("GBPJPY", gbpjpy_h4))
    _, aud_h4, _ = prepared["AUDUSD"]
    frames.append(study._audusd_candidates(aud_h4, AUDUSD_PARAMS))
    _, jpy_h4, _ = prepared["USDJPY"]
    frames.append(study._usdjpy_candidates(jpy_h4))
    usable = [frame for frame in frames if not frame.empty]
    if not usable:
        return pd.DataFrame()
    return pd.concat(usable, ignore_index=True).sort_values(
        ["entry_time", "engine", "setup"]
    ).drop_duplicates(["entry_time", "engine", "setup", "side"]).reset_index(drop=True)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _signal_key(row) -> str:
    stamp = pd.Timestamp(row.entry_time).isoformat()
    return f"{row.symbol}:{row.engine}:{row.setup}:{int(row.side)}:{stamp}"


def _atr_for_signal(prepared, row) -> float:
    timeframe = "H1" if str(row.setup) == "H1_BREAKOUT_RETEST" else "H4"
    frame = prepared[str(row.symbol)][0 if timeframe == "H1" else 1]
    stamp = pd.Timestamp(row.entry_time)
    matches = frame[frame["end"] == stamp]
    if matches.empty:
        raise RuntimeError(f"ATR row missing for {_signal_key(row)}")
    return float(matches.iloc[-1]["atr14"])


def _proposal_callback(summary) -> bool:
    return True


def scan_once(client, adapter: FinalV12Adapter, state_path: Path,
              proposal_log: Path, lookback_hours: int = 8) -> list[dict]:
    prepared = {symbol: prepare_live_frames(client, symbol) for symbol in SYMBOLS}
    candidates = build_final_candidates(prepared)
    if candidates.empty:
        return []
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=lookback_hours)
    recent = candidates[candidates["entry_time"] >= cutoff]
    state = _load_state(state_path)
    seen = set(state.get("seen", []))
    emitted: list[dict] = []
    for row in recent.itertuples(index=False):
        key = _signal_key(row)
        if key in seen:
            continue
        stop_atr, target_r = EXIT_MAP[(str(row.engine), str(row.setup))]
        atr_value = _atr_for_signal(prepared, row)
        pip = pip_size(client, str(row.symbol))
        if pip is None or not np.isfinite(atr_value) or atr_value <= 0:
            continue
        signal = NamedEngineSignal(
            symbol=str(row.symbol),
            engine=str(row.engine),
            setup=str(row.setup),
            side="BUY" if int(row.side) > 0 else "SELL",
            base_risk_percent=float(row.risk_percent),
            stop_pips=float(atr_value * stop_atr / pip),
            target_pips=float(atr_value * stop_atr * target_r / pip),
            signal_time=pd.Timestamp(row.entry_time).to_pydatetime(),
        )
        result = adapter.submit(signal)
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "signal_key": key,
            "signal": asdict(signal),
            "result": {
                "ok": result.ok,
                "code": result.code,
                "message": result.message,
                "volume": result.volume,
                "risk_percent": result.risk_percent,
                "proposal": asdict(result.proposal) if result.proposal else None,
            },
        }
        with proposal_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
        print(json.dumps(payload, indent=2, default=str))
        emitted.append(payload)
        seen.add(key)
    state["seen"] = sorted(seen)[-5000:]
    _save_state(state_path, state)
    return emitted


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the final V12 named-engine scanner")
    parser.add_argument("--once", action="store_true", help="Scan once and exit")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between scans")
    parser.add_argument("--lookback-hours", type=int, default=8)
    args = parser.parse_args()

    settings = load_settings()
    client = create_client()
    connect(client, settings)
    adapter = FinalV12Adapter(
        client,
        state_path=os.getenv("V12_FINAL_STATE_PATH", "v12_final_research_state.json"),
        approval_callback=_proposal_callback,
        max_deviation_points=int(os.getenv("V12_FINAL_MAX_DEVIATION_POINTS", "10")),
    )
    try:
        while True:
            try:
                emitted = scan_once(
                    client, adapter, STATE_FILE, PROPOSAL_LOG,
                    lookback_hours=args.lookback_hours,
                )
                if not emitted:
                    print(f"{datetime.now(timezone.utc).isoformat()} no new V12 signals")
            except Exception as exc:
                print(f"runner error: {type(exc).__name__}: {exc}", file=sys.stderr)
            if args.once:
                break
            time.sleep(max(15, args.interval))
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
