"""V14.6: regenerate V12 swing signals from exported MT5 data (2011-2026)
and run the continuous cost-aware ten-year backtest.

Uses the exact frozen research functions (study._gbpusd_precision,
study._v12_core_candidates, study._audusd_candidates, retest/safe-haven
generators) via mt5_ai_bridge.v14_3_live_signals.build_v12_candidates,
driven by a CSV-backed fake client that serves research/data_v14_6 bars.

Outputs:
  research/v14_6_output/regenerated_swing_trades.csv
  research/v14_6_output/v14_6_results.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from mt5_ai_bridge import v14_3_live_signals as live  # noqa: E402

DATA = ROOT / "research" / "data_v14_6"
OUT = ROOT / "research" / "v14_6_output"
COMBINED = ROOT / "research" / "v14_3_true_combined_v12_ict_output" / "true_combined_closed_trades.csv"

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
PROMOTED = {
    "GBPUSD_V10_PRECISION",
    "GBPJPY_SWING_CORE",
    "AUDUSD_TREND_PULLBACK",
    "EURUSD_SWING_CORE",
}
V14_5_RISK = 0.75
# V14.5.1: AUDUSD_TREND_PULLBACK failed the same promotion rule on the
# regenerated 2022-2026 out-of-sample segment (-0.09R/trade, PF 0.83) and
# is demoted to observation, mirroring what the live V14.4 expectancy
# tracker would do. USDJPY regeneration is NOT ledger-parity (638 vs 114
# overlap trades - the ledger stream had additional selection) and stays
# excluded from promoted sets.
PROMOTED_V14_5_1 = PROMOTED - {"AUDUSD_TREND_PULLBACK"}
OBSERVATION_RISK = 0.025
MAX_OPEN_RISK = 3.25
STARTING_BALANCE = 5_000.0
COSTS = {
    "zero_cost": {"V12": 0.0, "ICT": 0.0},
    "demo_cost": {"V12": 0.02, "ICT": 0.075},
    "retail_cost": {"V12": 0.03, "ICT": 0.13},
}
GOVERNOR = ((9.6, 0.0), (9.0, 0.50), (8.5, 0.82), (7.5, 0.98))


class CSVClient:
    """Serves exported CSV bars with MT5 copy_rates_from_pos semantics."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.cache: dict[tuple[str, str], pd.DataFrame] = {}

    def _load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        if key not in self.cache:
            frame = pd.read_csv(self.data_dir / f"{symbol}_{timeframe}.csv")
            frame["time"] = (
                pd.to_datetime(frame["time"], utc=True).astype("int64") // 10**9
            )
            if "tick_volume" not in frame.columns:
                frame["tick_volume"] = 0
            self.cache[key] = frame[
                ["time", "open", "high", "low", "close", "tick_volume"]
            ].sort_values("time").reset_index(drop=True)
        return self.cache[key]

    def copy_rates_from_pos(self, symbol: str, timeframe: str, start: int, count: int):
        frame = self._load(symbol, timeframe)
        end = len(frame) - start
        begin = max(0, end - count)
        if end <= 0:
            return []
        return self.cache[(symbol, timeframe)].iloc[begin:end].to_dict("records")


def governed_risk(risk: float, drawdown: float) -> float:
    for threshold, multiplier in GOVERNOR:
        if drawdown >= threshold:
            if multiplier <= 0:
                return min(risk, OBSERVATION_RISK)
            return risk * multiplier
    return risk


def replay(trades: list[dict], model: str, costs: dict, window_start=None) -> dict:
    equity = STARTING_BALANCE
    peak = equity
    max_dd = 0.0
    open_positions: list[tuple[pd.Timestamp, float]] = []
    n = 0
    gross_profit = gross_loss = 0.0
    by_year: dict[int, float] = {}
    by_engine: dict[str, float] = {}
    first = last = None
    for trade in trades:
        if window_start is not None and trade["entry"] < window_start:
            continue
        open_positions = [p for p in open_positions if p[0] > trade["entry"]]
        if model == "baseline":
            risk = float(trade["ledger_risk"])
        else:
            promoted = PROMOTED_V14_5_1 if model == "v14_5_1" else PROMOTED
            if trade["group"] == "ICT":
                risk = OBSERVATION_RISK
            elif trade["engine"] in promoted:
                risk = V14_5_RISK
            else:
                risk = 0.0
        if risk <= 0:
            continue
        drawdown = max(0.0, (peak - equity) / peak * 100.0) if peak > 0 else 0.0
        risk = governed_risk(risk, drawdown)
        open_risk = sum(p[1] for p in open_positions)
        if open_risk + risk > MAX_OPEN_RISK + 1e-9:
            continue
        open_positions.append((trade["exit"], risk))
        net_r = trade["r"] - costs[trade["group"]]
        pnl = equity * risk / 100.0 * net_r
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, max(0.0, (peak - equity) / peak * 100.0))
        n += 1
        first = first or trade["entry"]
        last = trade["exit"] if last is None else max(last, trade["exit"])
        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss -= pnl
        by_year[trade["exit"].year] = by_year.get(trade["exit"].year, 0.0) + pnl
        by_engine[trade["engine"]] = by_engine.get(trade["engine"], 0.0) + pnl
    years = max((last - first).days / 365.25, 1e-9) if first is not None else 0.0
    return {
        "trades": n,
        "ending_balance": round(equity, 2),
        "net_profit": round(equity - STARTING_BALANCE, 2),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown_percent": round(max_dd, 4),
        "years": round(years, 2),
        "cagr_percent": round(((equity / STARTING_BALANCE) ** (1 / years) - 1) * 100, 2)
        if years > 0 and equity > 0
        else None,
        "net_by_year": {str(k): round(v, 2) for k, v in sorted(by_year.items())},
        "net_by_engine": {
            k: round(v, 2)
            for k, v in sorted(by_engine.items(), key=lambda kv: -kv[1])
        },
    }


def engine_stats(frame: pd.DataFrame, start, end) -> dict:
    window = frame[(frame["entry_time"] >= start) & (frame["entry_time"] <= end)]
    out = {}
    for engine, group in window.groupby("engine"):
        rs = group["r_multiple"].astype(float)
        gp = rs[rs > 0].sum()
        gl = -rs[rs < 0].sum()
        out[str(engine)] = {
            "trades": int(len(rs)),
            "sum_r": round(float(rs.sum()), 2),
            "expectancy_r": round(float(rs.mean()), 4) if len(rs) else None,
            "profit_factor": round(float(gp / gl), 3) if gl > 0 else None,
        }
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = CSVClient(DATA)
    print("Preparing frames from exported data...", flush=True)
    prepared = {
        symbol: live.prepare_v12_frames(
            client, symbol, h1_count=3000, h4_count=23900, d1_count=3980
        )
        for symbol in SYMBOLS
    }
    for symbol in SYMBOLS:
        h1, h4, d1 = prepared[symbol]
        print(f"  {symbol}: H1={len(h1)} H4={len(h4)} D1={len(d1)} "
              f"({h4['time'].min()} -> {h4['time'].max()})", flush=True)

    print("Generating candidates with frozen research functions...", flush=True)
    # Mirror live.build_v12_candidates but skip the H1 retest micro engine
    # (demoted, H1-based, memory-heavy over 15 years of bars).
    frames = []
    _, gbp_h4, _ = prepared["GBPUSD"]
    frames.extend([live.study._gbpusd_precision(gbp_h4), live.study._gbpusd_retest_candidates(gbp_h4)])
    frames.append(live.study._v12_core_candidates("EURUSD", prepared["EURUSD"][1]))
    frames.append(live.study._v12_core_candidates("GBPJPY", prepared["GBPJPY"][1]))
    frames.append(live.study._audusd_candidates(prepared["AUDUSD"][1], live.AUDUSD_PARAMS))
    frames.append(live.study._usdjpy_candidates(prepared["USDJPY"][1]))
    usable = [f for f in frames if not f.empty]
    candidates = pd.concat(usable, ignore_index=True).sort_values(["entry_time", "engine", "setup"])
    candidates = candidates.drop_duplicates(["entry_time", "engine", "setup", "side"]).reset_index(drop=True)
    candidates = live.apply_satellite_v12_risk(live.apply_weak_symbol_profile(candidates))
    candidates["entry_time"] = pd.to_datetime(candidates["entry_time"], utc=True)
    candidates["exit_time"] = pd.to_datetime(candidates["exit_time"], utc=True)
    candidates = candidates.sort_values("entry_time").reset_index(drop=True)
    candidates.to_csv(OUT / "regenerated_swing_trades.csv", index=False)
    print(f"Regenerated {len(candidates)} swing trades "
          f"({candidates['entry_time'].min()} -> {candidates['entry_time'].max()})", flush=True)

    # ---- validation against the frozen ledger (overlap 2013-02 .. 2022-03) --
    combined = pd.read_csv(COMBINED)
    combined["entry_time"] = pd.to_datetime(combined["entry_time"], utc=True)
    combined["exit_time"] = pd.to_datetime(combined["exit_time"], utc=True)
    ledger = combined[combined["engine_group"] == "V12"]
    overlap_start = ledger["entry_time"].min()
    overlap_end = ledger["entry_time"].max()
    ledger_stats = {}
    for engine, group in ledger.groupby("engine"):
        rs = group["r_multiple"].astype(float)
        gp = rs[rs > 0].sum()
        gl = -rs[rs < 0].sum()
        ledger_stats[str(engine)] = {
            "trades": int(len(rs)),
            "sum_r": round(float(rs.sum()), 2),
            "expectancy_r": round(float(rs.mean()), 4),
            "profit_factor": round(float(gp / gl), 3) if gl > 0 else None,
        }
    regen_overlap = engine_stats(candidates, overlap_start, overlap_end)
    regen_oos = engine_stats(
        candidates,
        pd.Timestamp("2022-03-05", tz="UTC"),
        candidates["entry_time"].max(),
    )

    # ---- continuous replay ----------------------------------------------
    v12_trades = [
        {
            "entry": row.entry_time,
            "exit": row.exit_time,
            "engine": str(row.engine),
            "group": "V12",
            "r": float(row.r_multiple),
            "ledger_risk": float(row.risk_percent),
        }
        for row in candidates.itertuples(index=False)
    ]
    ict = combined[combined["engine_group"] == "ICT"]
    ict_trades = [
        {
            "entry": row.entry_time,
            "exit": row.exit_time,
            "engine": str(row.engine),
            "group": "ICT",
            "r": float(row.r_multiple),
            "ledger_risk": float(row.risk_percent),
        }
        for row in ict.itertuples(index=False)
    ]
    all_trades = sorted(v12_trades + ict_trades, key=lambda t: (t["entry"], t["exit"]))
    latest = max(t["exit"] for t in all_trades)
    ten_year_start = latest - timedelta(days=3652)

    results = {}
    for model in ("baseline", "v14_5", "v14_5_1"):
        for cost_name, costs in COSTS.items():
            results[f"ten_year/{model}/{cost_name}"] = replay(
                all_trades, model, costs, ten_year_start
            )

    payload = {
        "generated_at": datetime.now().isoformat(),
        "regenerated_trades": int(len(candidates)),
        "data_span": [str(candidates["entry_time"].min()), str(candidates["entry_time"].max())],
        "ten_year_window_start": str(ten_year_start),
        "validation_overlap": {
            "window": [str(overlap_start), str(overlap_end)],
            "ledger": ledger_stats,
            "regenerated": regen_overlap,
        },
        "regenerated_oos_2022_2026": regen_oos,
        "results": results,
    }
    (OUT / "v14_6_results.json").write_text(json.dumps(payload, indent=2))

    print("\n=== Validation overlap (ledger vs regenerated, 2013-02..2022-03) ===")
    for engine in sorted(set(ledger_stats) | set(regen_overlap)):
        a = ledger_stats.get(engine)
        b = regen_overlap.get(engine)
        fmt = lambda s: (f"n={s['trades']:4d} R={s['sum_r']:+8.1f} exp={s['expectancy_r']:+.3f}" if s else "missing")
        print(f"{engine:28s} ledger: {fmt(a)} | regen: {fmt(b)}")
    print("\n=== Regenerated out-of-sample 2022-03 .. 2026-07 ===")
    for engine, s in sorted(regen_oos.items()):
        print(f"{engine:28s} n={s['trades']:4d} R={s['sum_r']:+8.1f} exp={s['expectancy_r']:+.4f} pf={s['profit_factor']}")
    print("\n=== Continuous ten-year replay ===")
    for key, res in results.items():
        print(f"{key:34s} n={res['trades']:5d} end={res['ending_balance']:10,.0f} "
              f"net={res['net_profit']:+10,.0f} pf={res['profit_factor'] or 0:.3f} "
              f"dd={res['max_drawdown_percent']:5.2f}% cagr={res['cagr_percent'] or 0:.2f}%")
    print(f"\nWritten to {OUT}")


if __name__ == "__main__":
    main()
