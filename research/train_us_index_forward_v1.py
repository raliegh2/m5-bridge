"""Train and evaluate US_INDEX_FORWARD_V1 on the committed US500 history."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from mt5_ai_bridge.instruments import INSTRUMENTS, cost_for, settle
from mt5_ai_bridge.us_index_forward_v1 import (
    LOCKED_CONFIG,
    backtest_frozen,
    fit_model,
    save_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "research" / "data" / "US500_D1.csv"
DEFAULT_ARTIFACT = ROOT / "research" / "us_index_forward_v1_model.json"
DEFAULT_RESULT = ROOT / "research" / "us_index_forward_v1_result.json"


def _max_drawdown(curve: list[float]) -> float:
    values = np.asarray(curve, dtype=float)
    if values.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(values)
    return float(np.max((peaks - values) / peaks))


def _net_of_costs(summary: dict, trade_log: list[dict], starting_balance: float
                  ) -> tuple[dict, list[dict]]:
    """Replace gross validation metrics with instrument-specific net results."""
    instrument = INSTRUMENTS[LOCKED_CONFIG.research_symbol]
    costs = cost_for(LOCKED_CONFIG.research_symbol, "typical")
    balance = float(starting_balance)
    curve = [balance]
    net_trades: list[dict] = []
    total_cost = 0.0

    for trade in trade_log:
        nights = max(
            0,
            int((int(trade["exit_time"]) - int(trade["entry_time"])) // 86_400),
        )
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
        net_trades.append(updated)

    profits = [float(t["profit"]) for t in net_trades]
    wins = [p for p in profits if p > 0]
    losses = [-p for p in profits if p < 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)
    pf = (
        gross_win / gross_loss
        if gross_loss > 0
        else float("inf") if gross_win > 0
        else 0.0
    )

    result = dict(summary)
    result["gross_final_balance_before_costs"] = summary["final_balance"]
    result["gross_return_pct_before_costs"] = summary["return_pct"]
    result["gross_profit_factor_before_costs"] = summary["profit_factor"]
    result["cost_model"] = "US500 typical instrument-specific spread/slippage/commission"
    result["total_cost"] = round(total_cost, 2)
    result["final_balance"] = round(balance, 2)
    result["return_pct"] = round((balance / starting_balance - 1.0) * 100.0, 3)
    result["max_drawdown_pct"] = round(_max_drawdown(curve) * 100.0, 3)
    result["win_rate"] = round(len(wins) / len(net_trades), 4) if net_trades else 0.0
    result["profit_factor"] = round(float(pf), 4) if np.isfinite(pf) else "inf"
    return result, net_trades


def _ready(summary: dict) -> tuple[bool, list[str]]:
    reasons = []
    if float(summary["training_years"]) < 5.0:
        reasons.append("training span below five years")
    if float(summary["return_pct"]) <= 0:
        reasons.append("post-cutoff net return is not positive")
    pf = summary["profit_factor"]
    pf_value = float("inf") if pf == "inf" else float(pf)
    if pf_value < 1.0:
        reasons.append("post-cutoff net profit factor below 1.0")
    if float(summary["max_drawdown_pct"]) > 10.0:
        reasons.append("post-cutoff max drawdown above 10%")
    if int(summary["trades"]) < 40:
        reasons.append("fewer than 40 post-cutoff trades")
    return not reasons, reasons


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--balance", type=float, default=10_000.0)
    args = parser.parse_args(argv)

    bars = pd.read_csv(args.data)
    artifact = fit_model(bars, LOCKED_CONFIG)
    save_artifact(artifact, args.artifact)

    gross = backtest_frozen(bars, artifact, LOCKED_CONFIG, args.balance)
    trade_log = gross.pop("trade_log")
    full, trade_log = _net_of_costs(gross, trade_log, args.balance)
    ready, reasons = _ready(full)
    full["forward_test_ready"] = ready
    full["forward_test_blockers"] = reasons
    full["execution_scope"] = "demo-forward-test-only"
    full["options_enabled"] = False
    full["notes"] = (
        "The artifact is fit only through 2020-12-31. All later bars are "
        "evaluated without refitting and the gate is scored net of the repo's "
        "typical US500 trading-cost model. Passing authorizes demo forward "
        "testing only; it does not authorize funded/live trading."
    )

    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(full, indent=2), encoding="utf-8")
    ledger = args.result.with_name(args.result.stem + "_ledger.json")
    ledger.write_text(json.dumps(trade_log, indent=2), encoding="utf-8")

    print(json.dumps(full, indent=2))
    print(f"artifact: {args.artifact}")
    print(f"ledger:   {ledger}")
    return 0 if ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
