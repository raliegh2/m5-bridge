"""Train US_INDEX_FORWARD_V2 and compare it with V1 on a $5,000 account."""
from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd

from mt5_ai_bridge.instruments import INSTRUMENTS, cost_for, settle
from mt5_ai_bridge.us_index_forward_v1 import (
    LOCKED_CONFIG as V1_CONFIG,
    backtest_frozen as backtest_v1,
    load_artifact as load_v1,
)
from mt5_ai_bridge.us_index_forward_v2 import (
    LOCKED_CONFIG,
    backtest_window,
    fit_model,
    save_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "research" / "data" / "US500_D1.csv"
DEFAULT_ARTIFACT = ROOT / "research" / "us_index_forward_v2_model.json"
DEFAULT_RESULT = ROOT / "research" / "us_index_forward_v2_result.json"
V1_ARTIFACT = ROOT / "research" / "us_index_forward_v1_model.json"


def _max_drawdown(curve: list[float]) -> float:
    values = np.asarray(curve, dtype=float)
    if values.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(values)
    return float(np.max((peaks - values) / peaks))


def _net_of_costs(summary: dict, trade_log: list[dict], starting_balance: float,
                  symbol: str = "US500") -> tuple[dict, list[dict], list[dict]]:
    instrument = INSTRUMENTS[symbol]
    costs = cost_for(symbol, "typical")
    balance = float(starting_balance)
    curve = [balance]
    snapshots: list[dict] = []
    net_trades: list[dict] = []
    total_cost = 0.0

    for trade in trade_log:
        nights = max(0, int((int(trade["exit_time"]) - int(trade["entry_time"])) // 86_400))
        gross, trade_cost = settle(
            instrument,
            trade["side"],
            float(trade["lots"]),
            float(trade["entry"]),
            float(trade["exit"]),
            nights,
            costs,
            int(trade["exit_time"]),
        )
        net = float(gross) - float(trade_cost)
        balance += net
        total_cost += float(trade_cost)
        curve.append(balance)
        updated = dict(trade)
        updated["gross_profit"] = round(float(gross), 2)
        updated["cost"] = round(float(trade_cost), 2)
        updated["profit"] = round(net, 2)
        updated["_profit_exact"] = net
        updated["balance_after"] = round(balance, 2)
        net_trades.append(updated)
        snapshots.append({"time": int(trade["exit_time"]), "balance": round(balance, 2)})

    profits = [float(t["_profit_exact"]) for t in net_trades]
    wins = [p for p in profits if p > 0]
    losses = [-p for p in profits if p < 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

    result = dict(summary)
    result.pop("equity_snapshots", None)
    result["gross_final_balance_before_costs"] = summary["final_balance"]
    result["gross_return_pct_before_costs"] = summary["return_pct"]
    result["gross_profit_factor_before_costs"] = summary["profit_factor"]
    result["cost_model"] = f"{symbol} typical instrument-specific spread/slippage/commission"
    result["total_cost"] = round(total_cost, 2)
    result["final_balance"] = round(balance, 2)
    result["net_profit"] = round(balance - starting_balance, 2)
    result["return_pct"] = round((balance / starting_balance - 1.0) * 100.0, 4)
    result["max_drawdown_pct"] = round(_max_drawdown(curve) * 100.0, 4)
    result["win_rate"] = round(len(wins) / len(net_trades), 4) if net_trades else 0.0
    result["profit_factor"] = round(float(pf), 4) if np.isfinite(pf) else "inf"
    return result, net_trades, snapshots


def _annual_returns(net_trades: list[dict], starting_balance: float,
                    validation_start: int, validation_end: int) -> list[dict]:
    start_year = pd.to_datetime(validation_start, unit="s", utc=True).year
    end_year = pd.to_datetime(validation_end, unit="s", utc=True).year
    by_year: OrderedDict[int, list[dict]] = OrderedDict((y, []) for y in range(start_year, end_year + 1))
    for trade in net_trades:
        year = pd.to_datetime(int(trade["exit_time"]), unit="s", utc=True).year
        by_year.setdefault(year, []).append(trade)

    result = []
    balance = float(starting_balance)
    for year, trades in by_year.items():
        year_start = balance
        profit = sum(float(t.get("_profit_exact", t["profit"])) for t in trades)
        balance += profit
        result.append({
            "year": int(year),
            "start_balance": round(year_start, 2),
            "profit": round(profit, 2),
            "end_balance": round(balance, 2),
            "return_pct": round((profit / year_start) * 100.0, 3) if year_start else 0.0,
            "trades": len(trades),
            "period": "YTD" if year == end_year else "full_year",
        })
    return result


def _month_key_from_epoch(value: int) -> str:
    return str(pd.to_datetime(int(value), unit="s", utc=True).tz_convert(None).to_period("M"))


def _monthly_equity(net_trades: list[dict], starting_balance: float,
                    validation_start: int, validation_end: int) -> list[dict]:
    start = pd.to_datetime(validation_start, unit="s", utc=True).tz_convert(None).to_period("M")
    end = pd.to_datetime(validation_end, unit="s", utc=True).tz_convert(None).to_period("M")
    months = pd.period_range(start, end, freq="M")
    buckets: dict[str, float] = {str(m): 0.0 for m in months}
    for t in net_trades:
        key = _month_key_from_epoch(int(t["exit_time"]))
        buckets[key] = buckets.get(key, 0.0) + float(t.get("_profit_exact", t["profit"]))
    balance = float(starting_balance)
    output = []
    for month in months:
        key = str(month)
        balance += buckets.get(key, 0.0)
        output.append({
            "month": key,
            "balance": round(balance, 2),
            "cumulative_profit": round(balance - starting_balance, 2),
        })
    return output


def _ready(summary: dict, v1: dict) -> tuple[bool, list[str]]:
    reasons = []
    if float(summary["training_years"]) < 5.0:
        reasons.append("training span below five years")
    if float(summary["return_pct"]) <= 0:
        reasons.append("V2 post-cutoff net return is not positive")
    pf = summary["profit_factor"]
    pfv = float("inf") if pf == "inf" else float(pf)
    if pfv < 1.05:
        reasons.append("V2 net profit factor below 1.05")
    if float(summary["max_drawdown_pct"]) > 10.0:
        reasons.append("V2 max drawdown above 10%")
    if int(summary["trades"]) < 35:
        reasons.append("V2 has fewer than 35 post-cutoff trades")
    v2_ratio = float(summary["return_pct"]) / max(float(summary["max_drawdown_pct"]), 0.25)
    v1_ratio = float(v1["return_pct"]) / max(float(v1["max_drawdown_pct"]), 0.25)
    stronger = (
        float(summary["return_pct"]) >= float(v1["return_pct"]) + 0.50
        or v2_ratio >= v1_ratio * 1.15
    ) and float(summary["max_drawdown_pct"]) <= float(v1["max_drawdown_pct"]) + 1.0
    if not stronger:
        reasons.append("V2 did not beat the locked V1 strength rule on the untouched holdout")
    return not reasons, reasons


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--balance", type=float, default=5_000.0)
    args = parser.parse_args(argv)

    bars = pd.read_csv(args.data)
    artifact = fit_model(bars, LOCKED_CONFIG)
    save_artifact(artifact, args.artifact)

    cutoff = int(pd.Timestamp(LOCKED_CONFIG.training_cutoff, tz="UTC").timestamp())
    validation_start = int(bars.loc[bars["time"] > cutoff, "time"].min())
    validation_end = int(bars["time"].max())

    v2_gross = backtest_window(
        bars, artifact, LOCKED_CONFIG, args.balance,
        start_time=validation_start, end_time=validation_end,
    )
    v2_trade_log = v2_gross.pop("trade_log")
    v2, v2_trade_log, _ = _net_of_costs(v2_gross, v2_trade_log, args.balance)
    v2.update({
        "training_start": artifact.training_start,
        "training_end": artifact.training_end,
        "training_years": artifact.training_years,
        "training_rows": artifact.training_rows,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "selected_candidate": artifact.candidate["name"],
        "selection_report": artifact.selection_report,
    })

    if not V1_ARTIFACT.exists():
        raise FileNotFoundError("V1 artifact is required for locked $5k comparison")
    v1_artifact = load_v1(V1_ARTIFACT)
    v1_gross = backtest_v1(bars, v1_artifact, V1_CONFIG, args.balance)
    v1_trade_log = v1_gross.pop("trade_log")
    v1, _, _ = _net_of_costs(v1_gross, v1_trade_log, args.balance)

    annual = _annual_returns(v2_trade_log, args.balance, validation_start, validation_end)
    monthly = _monthly_equity(v2_trade_log, args.balance, validation_start, validation_end)
    ready, blockers = _ready(v2, v1)
    result = dict(v2)
    result["annual_returns"] = annual
    result["monthly_equity"] = monthly
    result["comparison_v1_5000"] = {
        "final_balance": v1["final_balance"],
        "net_profit": v1["net_profit"],
        "return_pct": v1["return_pct"],
        "max_drawdown_pct": v1["max_drawdown_pct"],
        "trades": v1["trades"],
        "win_rate": v1["win_rate"],
        "profit_factor": v1["profit_factor"],
    }
    result["forward_test_ready"] = ready
    result["forward_test_blockers"] = blockers
    result["execution_scope"] = "demo-forward-test-only"
    result["starting_account_model"] = 5000.0
    result["notes"] = (
        "V2 candidate selection used only pre-2021 walk-forward folds. The 2021+ "
        "window remained untouched until this one final V1-vs-V2 comparison. "
        "Results include the repo's typical US500 cost model and are research/demo "
        "estimates, not guaranteed future returns."
    )

    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    ledger = args.result.with_name(args.result.stem + "_ledger.json")
    public_ledger = []
    for trade in v2_trade_log:
        row = dict(trade)
        row.pop("_profit_exact", None)
        public_ledger.append(row)
    ledger.write_text(json.dumps(public_ledger, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    print(f"artifact: {args.artifact}")
    print(f"ledger:   {ledger}")
    return 0 if ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
