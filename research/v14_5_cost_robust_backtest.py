"""V14.5 cost-robust reallocation backtest.

Replays the repository's combined V12 + ICT trade streams chronologically
with realistic per-trade costs and compares the current model against the
V14.5 reallocation.

Motivation (July 2026 research pass)
------------------------------------
* The GBP ICT M1 stream (11,649 candidates, 2023-01 -> 2026-07) has a raw
  expectancy of +0.05..0.08R per trade. At its 5.0-7.5 pip stop floors,
  demo costs are ~0.05-0.10R and standard retail costs are ~0.10-0.16R per
  trade. No pre-registered filter variant (locked live rules, sweeps-only,
  setup whitelists, session windows) kept a positive net expectancy in the
  2025 confirmation year at those costs. The engine is structurally
  cost-broken at its stop sizes, not merely unlucky.
* The V12 swing ledger (918 trades, 2013-02 -> 2022-03, median hold 28h,
  ATR stops where spread is ~0.02-0.03R) contains engines whose per-trade
  expectancy is stable or improves out-of-sample (2013-2018 vs 2019-2022):

      GBPUSD_V10_PRECISION   +0.50R IS -> +0.56R OOS
      GBPJPY_SWING_CORE      +0.16R IS -> +0.42R OOS
      AUDUSD_TREND_PULLBACK  +0.14R IS -> +0.19R OOS
      EURUSD_SWING_CORE      +0.08R IS -> +0.32R OOS

  while EURUSD_SWING_RETEST and USDJPY_SAFE_HAVEN_BREAKOUT flipped negative
  out-of-sample and GBPUSD_SWING_RETEST never cleared costs.

V14.5 model (pre-registered, three parameters)
----------------------------------------------
1. PROMOTED swing engines (positive expectancy in BOTH halves and at least
   0.07R in both, i.e. ~3x swing cost): risk 0.75% per trade (below the
   0.80% parity ceiling).
2. All other V12 engines: dropped (live: micro observation).
3. ICT M1 stream: observation risk 0.025% per trade under the current
   locked live filters (kept only as a forward data feed).
Portfolio: 3.25% combined open-risk cap and the parity drawdown governor
(7.5/0.98, 8.5/0.82, 9.0/0.50, 9.6 stop) applied identically to all models.

Run:  python research/v14_5_cost_robust_backtest.py
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMBINED = ROOT / "research" / "v14_3_true_combined_v12_ict_output" / "true_combined_closed_trades.csv"
OUT_DIR = ROOT / "research" / "v14_5_cost_robust_output"

STARTING_BALANCE = 5_000.0

PROMOTED_V12 = {
    "GBPUSD_V10_PRECISION",
    "GBPJPY_SWING_CORE",
    "AUDUSD_TREND_PULLBACK",
    "EURUSD_SWING_CORE",
}
DEMOTED_V12 = {
    "GBPUSD_SWING_RETEST",
    "EURUSD_SWING_RETEST",
    "USDJPY_SAFE_HAVEN_BREAKOUT",
}

V14_5_PROMOTED_RISK = 0.75
OBSERVATION_RISK = 0.025
MAX_COMBINED_OPEN_RISK = 3.25

# Per-trade cost in R (cost = spread / stop distance).
# Swing: ATR stops of 40-90 pips -> ~0.02-0.03R.  ICT M1: 5-7.5 pip stops.
COST_SCENARIOS = {
    "zero_cost": {"V12": 0.0, "ICT": 0.0},
    "demo_cost": {"V12": 0.02, "ICT": 0.075},
    "retail_cost": {"V12": 0.03, "ICT": 0.13},
}

GOVERNOR = ((9.6, 0.0), (9.0, 0.50), (8.5, 0.82), (7.5, 0.98))


def governed_risk(risk: float, drawdown_percent: float) -> float:
    """Parity governor tiers. In the live bot the 9.6% tier is a hard stop;
    in this replay it trades at the 0.025% observation floor instead so a
    frozen account cannot deadlock the simulation (documented deviation)."""
    for threshold, multiplier in GOVERNOR:
        if drawdown_percent >= threshold:
            if multiplier <= 0:
                return min(risk, OBSERVATION_RISK)
            return risk * multiplier
    return risk


def load_trades() -> list[dict]:
    rows = list(csv.DictReader(open(COMBINED, encoding="utf-8")))
    trades = []
    for row in rows:
        trades.append(
            {
                "engine_group": row["engine_group"],
                "engine": row["engine"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "entry": datetime.fromisoformat(row["entry_time"]),
                "exit": datetime.fromisoformat(row["exit_time"]),
                "r": float(row["r_multiple"]),
                "ledger_risk": float(row["risk_percent"]),
            }
        )
    trades.sort(key=lambda t: (t["entry"], t["exit"]))
    return trades


def model_risk(trade: dict, model: str) -> float:
    """Per-trade risk percent under each model. 0 = skip."""
    if model == "baseline":
        return trade["ledger_risk"]
    if model == "v14_5":
        if trade["engine_group"] == "ICT":
            return OBSERVATION_RISK
        if trade["engine"] in PROMOTED_V12:
            return V14_5_PROMOTED_RISK
        return 0.0
    raise ValueError(model)


def replay(trades, model, costs, window_start=None):
    equity = STARTING_BALANCE
    peak = equity
    max_dd = 0.0
    open_positions = []  # (exit_time, risk%)
    n = wins = skipped = 0
    gross_profit = gross_loss = 0.0
    by_year = {}
    by_engine = {}
    first_entry = last_exit = None

    for trade in trades:
        if window_start is not None and trade["entry"] < window_start:
            continue
        open_positions = [p for p in open_positions if p[0] > trade["entry"]]

        risk = model_risk(trade, model)
        if risk <= 0:
            continue

        drawdown = max(0.0, (peak - equity) / peak * 100.0) if peak > 0 else 0.0
        risk = governed_risk(risk, drawdown)

        open_risk = sum(p[1] for p in open_positions)
        if open_risk + risk > MAX_COMBINED_OPEN_RISK + 1e-9:
            skipped += 1
            continue
        open_positions.append((trade["exit"], risk))

        net_r = trade["r"] - costs[trade["engine_group"]]
        pnl = equity * risk / 100.0 * net_r
        equity += pnl
        peak = max(peak, equity)
        drawdown_after = max(0.0, (peak - equity) / peak * 100.0)
        max_dd = max(max_dd, drawdown_after)

        n += 1
        wins += net_r > 0
        first_entry = first_entry or trade["entry"]
        last_exit = trade["exit"] if last_exit is None else max(last_exit, trade["exit"])
        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss -= pnl
        by_year[trade["exit"].year] = by_year.get(trade["exit"].year, 0.0) + pnl
        by_engine[trade["engine"]] = by_engine.get(trade["engine"], 0.0) + pnl

    years = (
        max((last_exit - first_entry).days / 365.25, 1e-9)
        if first_entry and last_exit
        else 0.0
    )
    return {
        "trades": n,
        "skipped": skipped,
        "ending_balance": round(equity, 2),
        "net_profit": round(equity - STARTING_BALANCE, 2),
        "return_percent": round((equity / STARTING_BALANCE - 1) * 100.0, 2),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "win_rate": round(wins / n, 4) if n else None,
        "max_drawdown_percent": round(max_dd, 4),
        "years": round(years, 2),
        "cagr_percent": (
            round(((equity / STARTING_BALANCE) ** (1.0 / years) - 1) * 100.0, 2)
            if years > 0
            else None
        ),
        "net_by_year": {str(k): round(v, 2) for k, v in sorted(by_year.items())},
        "net_by_engine": {k: round(v, 2) for k, v in sorted(by_engine.items(), key=lambda kv: -kv[1])},
    }


def main():
    trades = load_trades()
    latest_exit = max(t["exit"] for t in trades)
    ten_year_start = latest_exit - timedelta(days=3652)
    print(f"Loaded {len(trades)} combined trades "
          f"({min(t['entry'] for t in trades):%Y-%m-%d} -> {latest_exit:%Y-%m-%d})")
    print(f"Exact ten-year window starts {ten_year_start:%Y-%m-%d}\n")

    results = {}
    for window_name, window_start in (("full_history", None), ("exact_ten_year", ten_year_start)):
        for model in ("baseline", "v14_5"):
            for cost_name, costs in COST_SCENARIOS.items():
                key = f"{window_name}/{model}/{cost_name}"
                results[key] = replay(trades, model, costs, window_start)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "v14_5_results.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "starting_balance": STARTING_BALANCE,
                "promoted_v12": sorted(PROMOTED_V12),
                "demoted_v12": sorted(DEMOTED_V12),
                "v14_5_promoted_risk_percent": V14_5_PROMOTED_RISK,
                "ict_observation_risk_percent": OBSERVATION_RISK,
                "cost_scenarios_r": COST_SCENARIOS,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    header = f"{'scenario':44s} {'trades':>6s} {'ending':>11s} {'net':>11s} {'PF':>6s} {'maxDD%':>7s} {'CAGR':>6s}"
    print(header)
    print("-" * len(header))
    for key, res in results.items():
        print(
            f"{key:44s} {res['trades']:6d} {res['ending_balance']:11,.0f} "
            f"{res['net_profit']:+11,.0f} {res['profit_factor'] or 0:6.3f} "
            f"{res['max_drawdown_percent']:7.2f} {res['cagr_percent'] or 0:5.2f}%"
        )
    print(f"\nDetailed results written to {OUT_DIR / 'v14_5_results.json'}")


if __name__ == "__main__":
    main()
