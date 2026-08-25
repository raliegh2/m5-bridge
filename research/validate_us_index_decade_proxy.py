"""Ten-year out-of-sample proxy validation for the US index model architecture.

Uses IVV daily history because the repo's broker US500 series begins in 2012 and
cannot provide five training years plus a full ten-year untouched holdout.
Candidate selection and fitting are confined to data ending 2015-12-31. The
2016+ period is then evaluated once as a sealed decade-scale proxy test.

The IVV result is robustness evidence for the US-index architecture. It is not a
claim that ETF fills equal ES/MES/US500 futures or CFD fills.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from mt5_ai_bridge.us_index_forward_v2 import (
    USIndexForwardV2Config,
    _fit_candidate,
    backtest_window,
)
from mt5_ai_bridge.us_index_forward_v3 import CANDIDATES
from research.train_us_index_forward_v2 import _annual_returns, _monthly_equity

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research" / "data" / "IVV_D1.csv"
RESULT = ROOT / "research" / "us_index_decade_proxy_result.json"
LEDGER = ROOT / "research" / "us_index_decade_proxy_ledger.json"

CFG = USIndexForwardV2Config(
    research_symbol="IVV",
    timeframe="D1",
    fast_horizon=3,
    slow_horizon=10,
    atr_period=14,
    risk_percent=1.0,
    max_fraction_invested=0.70,
    min_training_years=5.0,
    training_cutoff="2015-12-31",
)

ROUND_TRIP_BPS = 5.0


def _score(summary: dict) -> float:
    pf = summary["profit_factor"]
    pfv = 3.0 if pf == "inf" else min(3.0, float(pf))
    trades = int(summary["trades"])
    return (
        float(summary["return_pct"])
        + 3.0 * (pfv - 1.0)
        - 0.65 * float(summary["max_drawdown_pct"])
        + 0.025 * min(trades, 50)
    )


def select_pre2016(bars: pd.DataFrame):
    folds = (
        ("2008-12-31", "2009-01-01", "2009-12-31"),
        ("2010-12-31", "2011-01-01", "2011-12-31"),
        ("2012-12-31", "2013-01-01", "2013-12-31"),
        ("2014-12-31", "2015-01-01", "2015-12-31"),
    )
    ordered = bars.sort_values("time").drop_duplicates("time")
    report = {}
    for candidate in CANDIDATES:
        fold_results = []
        for train_cutoff, val_start, val_end in folds:
            end_epoch = int(pd.Timestamp(val_end, tz="UTC").timestamp())
            fold_bars = ordered[ordered["time"] <= end_epoch].copy()
            fold_cfg = replace(CFG, training_cutoff=train_cutoff)
            try:
                artifact = _fit_candidate(fold_bars, candidate, fold_cfg)
                summary = backtest_window(
                    fold_bars,
                    artifact,
                    fold_cfg,
                    10_000.0,
                    int(pd.Timestamp(val_start, tz="UTC").timestamp()),
                    end_epoch,
                    min_lot=1.0,
                    lot_step=1.0,
                )
                fold_results.append({
                    "train_cutoff": train_cutoff,
                    "validation_year": val_start[:4],
                    "return_pct": summary["return_pct"],
                    "max_drawdown_pct": summary["max_drawdown_pct"],
                    "profit_factor": summary["profit_factor"],
                    "trades": summary["trades"],
                    "score": round(_score(summary), 4),
                })
            except ValueError as exc:
                fold_results.append({"train_cutoff": train_cutoff, "error": str(exc), "score": -999.0})
        valid = [f for f in fold_results if "error" not in f]
        scores = [float(f["score"]) for f in valid]
        positive = sum(1 for f in valid if float(f["return_pct"]) > 0)
        worst = min(scores) if scores else -999.0
        median = float(np.median(scores)) if scores else -999.0
        robust = median + 0.40 * worst + 0.60 * positive
        report[candidate.name] = {
            "folds": fold_results,
            "positive_folds": positive,
            "median_score": round(median, 4),
            "worst_score": round(worst, 4),
            "robust_score": round(robust, 4),
        }
    selected = max(CANDIDATES, key=lambda c: float(report[c.name]["robust_score"]))
    report["selected"] = selected.name
    report["selection_rule"] = "median + 0.40*worst + 0.60*positive folds; all folds end before 2016"
    return selected, report


def net_etf_costs(trades: list[dict], starting_balance: float):
    balance = float(starting_balance)
    curve = [balance]
    output = []
    total_cost = 0.0
    for trade in trades:
        side = 1.0 if trade["side"] == "BUY" else -1.0
        lots = float(trade["lots"])
        entry = float(trade["entry"])
        exit_ = float(trade["exit"])
        gross = (exit_ - entry) * side * lots
        cost = entry * lots * (ROUND_TRIP_BPS / 10_000.0)
        net = gross - cost
        balance += net
        total_cost += cost
        curve.append(balance)
        t = dict(trade)
        t["gross_profit"] = round(gross, 2)
        t["cost"] = round(cost, 2)
        t["profit"] = round(net, 2)
        t["_profit_exact"] = net
        t["balance_after"] = round(balance, 2)
        output.append(t)
    values = np.asarray(curve, dtype=float)
    peaks = np.maximum.accumulate(values)
    dd = float(np.max((peaks - values) / peaks)) if len(values) else 0.0
    profits = [float(t["_profit_exact"]) for t in output]
    wins = [x for x in profits if x > 0]
    losses = [-x for x in profits if x < 0]
    pf = sum(wins) / sum(losses) if losses else float("inf") if wins else 0.0
    return {
        "final_balance": round(balance, 2),
        "net_profit": round(balance - starting_balance, 2),
        "return_pct": round((balance / starting_balance - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(dd * 100.0, 4),
        "trades": len(output),
        "win_rate": round(len(wins) / len(output), 4) if output else 0.0,
        "profit_factor": round(pf, 4) if np.isfinite(pf) else "inf",
        "total_cost": round(total_cost, 2),
    }, output


def cagr_pct(start_balance: float, end_balance: float, start_time: int, end_time: int) -> float:
    years = (end_time - start_time) / (365.2425 * 86_400.0)
    return ((end_balance / start_balance) ** (1.0 / years) - 1.0) * 100.0


def main() -> int:
    bars = pd.read_csv(DATA)
    selected, selection_report = select_pre2016(bars)
    artifact = _fit_candidate(bars, selected, CFG, selection_report=selection_report)

    cutoff = int(pd.Timestamp(CFG.training_cutoff, tz="UTC").timestamp())
    start = int(bars.loc[bars["time"] > cutoff, "time"].min())
    end = int(bars["time"].max())
    gross = backtest_window(
        bars,
        artifact,
        CFG,
        5_000.0,
        start_time=start,
        end_time=end,
        min_lot=1.0,
        lot_step=1.0,
    )
    trade_log = gross.pop("trade_log")
    net, trade_log = net_etf_costs(trade_log, 5_000.0)
    cagr = cagr_pct(5_000.0, float(net["final_balance"]), start, end)
    years = (end - start) / (365.2425 * 86_400.0)

    result = {
        "test": "US_INDEX_DECADE_PROXY_IVV",
        "proxy_symbol": "IVV",
        "starting_balance": 5000.0,
        **net,
        "holdout_start": start,
        "holdout_end": end,
        "holdout_years": round(years, 4),
        "cagr_pct": round(cagr, 4),
        "training_cutoff": CFG.training_cutoff,
        "training_start": artifact.training_start,
        "training_end": artifact.training_end,
        "training_years": artifact.training_years,
        "training_rows": artifact.training_rows,
        "selected_candidate": selected.name,
        "selection_report": selection_report,
        "risk_percent_ceiling": CFG.risk_percent,
        "cost_assumption": f"{ROUND_TRIP_BPS:.1f} bps round trip on IVV entry notional",
        "annual_returns": _annual_returns(trade_log, 5_000.0, start, end),
        "monthly_equity": _monthly_equity(trade_log, 5_000.0, start, end),
        "decade_requirement_gt_10pct": bool(float(net["return_pct"]) > 10.0 and years >= 10.0),
        "notes": (
            "Candidate selection and fitting use only pre-2016 IVV data. 2016+ is a sealed "
            "proxy holdout. This validates architecture robustness over a decade but does "
            "not equate ETF execution costs or returns with futures/CFD execution."
        ),
    }
    RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    LEDGER.write_text(json.dumps(trade_log, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["decade_requirement_gt_10pct"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
