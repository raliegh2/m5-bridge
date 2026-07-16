"""V14.5.2 constrained adaptive-profit research.

Goal
----
Improve V14.5.1 profitability through better trade selection/risk allocation,
not by raising the 0.75% per-trade ceiling or weakening its protections.

Preserved from V14.5.1
----------------------
* same three promoted swing engines;
* 0.75% maximum promoted risk;
* 0.025% observation risk for all ICT and demoted swing streams;
* 3.25% combined open-risk cap;
* same 7.5/8.5/9.0/9.6 drawdown governor;
* same zero/demo/retail cost assumptions;
* no changes to entries, stops, targets, or broker transmission.

Improvement
-----------
A no-lookahead rolling expectancy gate uses only trades that CLOSED before a
new entry. A promoted engine remains full-size when its recent cost-adjusted
expectancy and profit factor are strong, moves to a reduced tier when marginal,
and moves to observation risk when weak. The parameter grid is deliberately
small and bounded. Candidates must improve the pre-2022 development segment
and pass a separate 2022-2026 validation gate before exact-ten-year reporting.

This is research only. It is an R-multiple replay, not tick simulation.
"""
from __future__ import annotations

import csv
import itertools
import json
import sys
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from mt5_ai_bridge import v14_3_live_signals as live  # noqa: E402
from mt5_ai_bridge.v14_5_cost_robust_profile import (  # noqa: E402
    PROMOTED_V12_ENGINES,
    V14_5_OBSERVATION_RISK_PERCENT,
    V14_5_PROMOTED_RISK_PERCENT,
)
from research.v14_6_swing_regeneration import CSVClient  # noqa: E402

DATA = ROOT / "research" / "data_v14_6"
COMBINED = (
    ROOT
    / "research"
    / "v14_3_true_combined_v12_ict_output"
    / "true_combined_closed_trades.csv"
)
OUT = ROOT / "research" / "v14_5_2_adaptive_profit_output"

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
STARTING_BALANCE = 5_000.0
PROMOTED_RISK = float(V14_5_PROMOTED_RISK_PERCENT)
OBSERVATION_RISK = float(V14_5_OBSERVATION_RISK_PERCENT)
MAX_OPEN_RISK = 3.25
GOVERNOR = ((9.6, 0.0), (9.0, 0.50), (8.5, 0.82), (7.5, 0.98))
COSTS = {
    "zero_cost": {"V12": 0.0, "ICT": 0.0},
    "demo_cost": {"V12": 0.02, "ICT": 0.075},
    "retail_cost": {"V12": 0.03, "ICT": 0.13},
}
VALIDATION_START = pd.Timestamp("2022-03-05", tz="UTC")


@dataclass(frozen=True)
class AdaptiveParams:
    lookback: int
    min_samples: int
    full_expectancy_r: float
    full_profit_factor: float
    reduced_expectancy_r: float
    reduced_profit_factor: float
    reduced_risk_percent: float


@dataclass
class OpenTrade:
    exit_time: pd.Timestamp
    engine: str
    group: str
    risk_percent: float
    risk_dollars: float
    raw_r: float
    net_r: float


def prepare_trade_stream() -> list[dict[str, Any]]:
    """Regenerate continuous 2011-2026 swing trades and append the ICT ledger."""
    client = CSVClient(DATA)
    prepared = {
        symbol: live.prepare_v12_frames(
            client,
            symbol,
            h1_count=3000,
            h4_count=23900,
            d1_count=3980,
        )
        for symbol in SYMBOLS
    }

    frames = []
    _, gbp_h4, _ = prepared["GBPUSD"]
    frames.extend(
        [
            live.study._gbpusd_precision(gbp_h4),
            live.study._gbpusd_retest_candidates(gbp_h4),
        ]
    )
    frames.append(live.study._v12_core_candidates("EURUSD", prepared["EURUSD"][1]))
    frames.append(live.study._v12_core_candidates("GBPJPY", prepared["GBPJPY"][1]))
    frames.append(live.study._audusd_candidates(prepared["AUDUSD"][1], live.AUDUSD_PARAMS))
    frames.append(live.study._usdjpy_candidates(prepared["USDJPY"][1]))
    usable = [frame for frame in frames if not frame.empty]
    swings = pd.concat(usable, ignore_index=True).sort_values(
        ["entry_time", "engine", "setup"]
    )
    swings = swings.drop_duplicates(
        ["entry_time", "engine", "setup", "side"]
    ).reset_index(drop=True)
    swings["entry_time"] = pd.to_datetime(swings["entry_time"], utc=True)
    swings["exit_time"] = pd.to_datetime(swings["exit_time"], utc=True)

    combined = pd.read_csv(COMBINED)
    combined["entry_time"] = pd.to_datetime(combined["entry_time"], utc=True)
    combined["exit_time"] = pd.to_datetime(combined["exit_time"], utc=True)
    ict = combined[combined["engine_group"] == "ICT"].copy()

    stream: list[dict[str, Any]] = []
    for row in swings.itertuples(index=False):
        stream.append(
            {
                "entry": row.entry_time,
                "exit": row.exit_time,
                "engine": str(row.engine),
                "symbol": str(row.symbol),
                "setup": str(row.setup),
                "group": "V12",
                "raw_r": float(row.r_multiple),
            }
        )
    for row in ict.itertuples(index=False):
        stream.append(
            {
                "entry": row.entry_time,
                "exit": row.exit_time,
                "engine": str(row.engine),
                "symbol": str(row.symbol),
                "setup": str(row.setup),
                "group": "ICT",
                "raw_r": float(row.r_multiple),
            }
        )
    stream.sort(key=lambda item: (item["entry"], item["exit"], item["engine"]))
    return stream


def governed_risk(risk: float, drawdown_percent: float) -> float:
    for threshold, multiplier in GOVERNOR:
        if drawdown_percent >= threshold:
            if multiplier <= 0:
                return min(risk, OBSERVATION_RISK)
            return risk * multiplier
    return risk


def profit_factor(values: list[float]) -> float:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = -sum(value for value in values if value < 0)
    if gross_loss <= 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def adaptive_requested_risk(
    engine: str,
    history: dict[str, deque[float]],
    params: AdaptiveParams,
) -> tuple[float, str, dict[str, float | int | None]]:
    if engine not in PROMOTED_V12_ENGINES:
        return OBSERVATION_RISK, "OBSERVATION_NON_PROMOTED", {
            "samples": 0,
            "expectancy_r": None,
            "profit_factor": None,
        }
    recent = list(history[engine])[-params.lookback :]
    if len(recent) < params.min_samples:
        return PROMOTED_RISK, "PROMOTED_COLD_START", {
            "samples": len(recent),
            "expectancy_r": None,
            "profit_factor": None,
        }
    expectancy = sum(recent) / len(recent)
    pf = profit_factor(recent)
    metrics: dict[str, float | int | None] = {
        "samples": len(recent),
        "expectancy_r": expectancy,
        "profit_factor": pf,
    }
    if expectancy >= params.full_expectancy_r and pf >= params.full_profit_factor:
        return PROMOTED_RISK, "ADAPTIVE_FULL", metrics
    if (
        expectancy >= params.reduced_expectancy_r
        and pf >= params.reduced_profit_factor
    ):
        return params.reduced_risk_percent, "ADAPTIVE_REDUCED", metrics
    return OBSERVATION_RISK, "ADAPTIVE_OBSERVATION", metrics


def static_requested_risk(trade: dict[str, Any]) -> tuple[float, str, dict[str, Any]]:
    if trade["group"] == "V12" and trade["engine"] in PROMOTED_V12_ENGINES:
        return PROMOTED_RISK, "STATIC_PROMOTED", {}
    return OBSERVATION_RISK, "STATIC_OBSERVATION", {}


def replay(
    trades: list[dict[str, Any]],
    model: str,
    costs: dict[str, float],
    start: pd.Timestamp,
    end: pd.Timestamp,
    params: AdaptiveParams | None = None,
    keep_ledger: bool = False,
) -> dict[str, Any]:
    equity = STARTING_BALANCE
    peak = equity
    max_dd = 0.0
    stress_dd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    open_trades: list[OpenTrade] = []
    history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
    by_engine: dict[str, float] = defaultdict(float)
    by_year: dict[int, float] = defaultdict(float)
    tiers: dict[str, int] = defaultdict(int)
    ledger: list[dict[str, Any]] = []
    skipped = 0

    def close_due(now: pd.Timestamp) -> None:
        nonlocal equity, peak, max_dd, gross_profit, gross_loss, wins
        due = sorted(
            [item for item in open_trades if item.exit_time <= now],
            key=lambda item: item.exit_time,
        )
        for item in due:
            pnl = item.risk_dollars * item.net_r
            equity += pnl
            peak = max(peak, equity)
            drawdown = max(0.0, (peak - equity) / peak * 100.0)
            max_dd = max(max_dd, drawdown)
            if pnl >= 0:
                gross_profit += pnl
            else:
                gross_loss -= pnl
            wins += int(item.net_r > 0)
            by_engine[item.engine] += pnl
            by_year[item.exit_time.year] += pnl
            if item.group == "V12" and item.engine in PROMOTED_V12_ENGINES:
                history[item.engine].append(item.net_r)
            open_trades.remove(item)

    for trade in trades:
        entry = trade["entry"]
        if entry < start or entry > end:
            continue
        close_due(entry)
        if model == "v14_5_1":
            requested, tier, metrics = static_requested_risk(trade)
        elif model == "v14_5_2":
            if trade["group"] == "ICT":
                requested, tier, metrics = OBSERVATION_RISK, "OBSERVATION_ICT", {}
            else:
                if params is None:
                    raise ValueError("Adaptive parameters are required")
                requested, tier, metrics = adaptive_requested_risk(
                    trade["engine"], history, params
                )
        else:
            raise ValueError(model)

        drawdown_before = max(0.0, (peak - equity) / peak * 100.0)
        approved = governed_risk(requested, drawdown_before)
        open_risk = sum(item.risk_percent for item in open_trades)
        if open_risk + approved > MAX_OPEN_RISK + 1e-12:
            skipped += 1
            continue
        net_r = trade["raw_r"] - costs[trade["group"]]
        risk_dollars = equity * approved / 100.0
        item = OpenTrade(
            exit_time=trade["exit"],
            engine=trade["engine"],
            group=trade["group"],
            risk_percent=approved,
            risk_dollars=risk_dollars,
            raw_r=trade["raw_r"],
            net_r=net_r,
        )
        open_trades.append(item)
        tiers[tier] += 1
        stressed_equity = equity - sum(active.risk_dollars for active in open_trades)
        stress_dd = max(
            stress_dd,
            max(0.0, (peak - stressed_equity) / peak * 100.0),
        )
        if keep_ledger:
            ledger.append(
                {
                    **trade,
                    "requested_risk_percent": requested,
                    "approved_risk_percent": approved,
                    "tier": tier,
                    "history_samples": metrics.get("samples"),
                    "history_expectancy_r": metrics.get("expectancy_r"),
                    "history_profit_factor": metrics.get("profit_factor"),
                    "net_r": net_r,
                    "risk_dollars": risk_dollars,
                    "equity_at_entry": equity,
                    "drawdown_at_entry": drawdown_before,
                }
            )
    close_due(pd.Timestamp.max.tz_localize("UTC"))
    trade_count = sum(tiers.values())
    return {
        "model": model,
        "trades": trade_count,
        "skipped": skipped,
        "ending_balance": round(equity, 2),
        "net_profit": round(equity - STARTING_BALANCE, 2),
        "return_percent": round((equity / STARTING_BALANCE - 1.0) * 100.0, 2),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "win_rate_percent": round(wins / trade_count * 100.0, 2) if trade_count else None,
        "max_drawdown_percent": round(max_dd, 4),
        "stress_drawdown_percent": round(stress_dd, 4),
        "net_by_engine": {
            key: round(value, 2)
            for key, value in sorted(by_engine.items(), key=lambda item: -item[1])
        },
        "net_by_year": {
            str(key): round(value, 2) for key, value in sorted(by_year.items())
        },
        "tier_counts": dict(sorted(tiers.items())),
        "ledger": ledger,
    }


def candidate_grid() -> list[AdaptiveParams]:
    return [
        AdaptiveParams(*values)
        for values in itertools.product(
            (12, 20, 30, 40),
            (8, 12),
            (0.04, 0.08, 0.12),
            (1.10, 1.20, 1.30),
            (0.00, 0.02),
            (1.00, 1.05),
            (0.25, 0.375, 0.50),
        )
        if values[1] <= values[0]
    ]


def select_params(
    trades: list[dict[str, Any]],
    ten_start: pd.Timestamp,
    latest: pd.Timestamp,
) -> tuple[AdaptiveParams, list[dict[str, Any]], dict[str, Any]]:
    development_end = VALIDATION_START - pd.Timedelta(microseconds=1)
    static_dev = replay(
        trades,
        "v14_5_1",
        COSTS["demo_cost"],
        ten_start,
        development_end,
    )
    static_validation = replay(
        trades,
        "v14_5_1",
        COSTS["demo_cost"],
        VALIDATION_START,
        latest,
    )
    ranked: list[dict[str, Any]] = []
    for params in candidate_grid():
        dev = replay(
            trades,
            "v14_5_2",
            COSTS["demo_cost"],
            ten_start,
            development_end,
            params,
        )
        if dev["net_profit"] <= static_dev["net_profit"]:
            continue
        if float(dev["profit_factor"] or 0.0) < float(static_dev["profit_factor"] or 0.0):
            continue
        if dev["max_drawdown_percent"] > static_dev["max_drawdown_percent"] + 0.50:
            continue
        score = (
            dev["net_profit"]
            + 350.0 * (float(dev["profit_factor"] or 0.0) - float(static_dev["profit_factor"] or 0.0))
            - 35.0 * max(0.0, dev["max_drawdown_percent"] - static_dev["max_drawdown_percent"])
        )
        ranked.append({"params": params, "development": dev, "score": score})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    if not ranked:
        raise RuntimeError("No adaptive candidate improved the development segment")

    chosen: dict[str, Any] | None = None
    for item in ranked:
        validation = replay(
            trades,
            "v14_5_2",
            COSTS["demo_cost"],
            VALIDATION_START,
            latest,
            item["params"],
        )
        item["validation"] = validation
        # Validation gate: preserve profitability and improve at least one of
        # profit factor or drawdown without materially damaging the other.
        profit_ok = validation["net_profit"] >= static_validation["net_profit"]
        pf_ok = float(validation["profit_factor"] or 0.0) >= float(static_validation["profit_factor"] or 0.0)
        dd_ok = validation["max_drawdown_percent"] <= static_validation["max_drawdown_percent"] + 0.25
        if profit_ok and pf_ok and dd_ok:
            chosen = item
            break
    if chosen is None:
        raise RuntimeError(
            "No development-selected candidate passed the 2022-2026 validation gates"
        )
    research = {
        "development_window": [str(ten_start), str(development_end)],
        "validation_window": [str(VALIDATION_START), str(latest)],
        "static_development": static_dev,
        "static_validation": static_validation,
        "selected_development": chosen["development"],
        "selected_validation": chosen["validation"],
        "candidates_evaluated": len(candidate_grid()),
        "development_candidates_passing": len(ranked),
    }
    compact_ranked = [
        {
            "rank": index + 1,
            "params": asdict(item["params"]),
            "score": round(float(item["score"]), 4),
            "development_net": item["development"]["net_profit"],
            "development_pf": item["development"]["profit_factor"],
            "development_dd": item["development"]["max_drawdown_percent"],
            "validation_net": item.get("validation", {}).get("net_profit"),
            "validation_pf": item.get("validation", {}).get("profit_factor"),
            "validation_dd": item.get("validation", {}).get("max_drawdown_percent"),
        }
        for index, item in enumerate(ranked[:50])
    ]
    return chosen["params"], compact_ranked, research


def serializable_result(result: dict[str, Any]) -> dict[str, Any]:
    output = dict(result)
    output.pop("ledger", None)
    return output


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(payload: dict[str, Any]) -> None:
    selected = payload["selected_params"]
    lines = [
        "# V14.5.2 Adaptive-Profit Research",
        "",
        "V14.5.2 improves V14.5.1 through no-lookahead rolling engine selection. It does not raise the 0.75% trade-risk ceiling, re-enable cost-broken ICT sizing, change stops/targets, or weaken the drawdown/open-risk protections.",
        "",
        "## Selected parameters",
        "",
        "```json",
        json.dumps(selected, indent=2),
        "```",
        "",
        "## Development and validation",
        "",
        "| Segment | Model | Net profit | PF | Max DD | Stress DD |",
        "|---|---|---:|---:|---:|---:|",
    ]
    research = payload["selection_research"]
    for label, key in (
        ("Development", "static_development"),
        ("Development", "selected_development"),
        ("Validation", "static_validation"),
        ("Validation", "selected_validation"),
    ):
        result = research[key]
        model = "V14.5.1" if "static" in key else "V14.5.2"
        lines.append(
            f"| {label} | {model} | ${result['net_profit']:,.2f} | {float(result['profit_factor'] or 0):.4f} | "
            f"{result['max_drawdown_percent']:.4f}% | {result['stress_drawdown_percent']:.4f}% |"
        )
    lines += [
        "",
        "## Exact ten-year comparison",
        "",
        "| Costs | Model | Net profit | Ending balance | PF | Max DD | Stress DD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for cost in COSTS:
        for model in ("v14_5_1", "v14_5_2"):
            result = payload["results"][f"ten_year/{model}/{cost}"]
            label = "V14.5.1" if model == "v14_5_1" else "V14.5.2"
            lines.append(
                f"| {cost} | {label} | ${result['net_profit']:,.2f} | ${result['ending_balance']:,.2f} | "
                f"{float(result['profit_factor'] or 0):.4f} | {result['max_drawdown_percent']:.4f}% | "
                f"{result['stress_drawdown_percent']:.4f}% |"
            )
    lines += [
        "",
        "## Boundaries",
        "",
        "- The rolling gate uses only closed prior trades; it does not use future outcomes at entry.",
        "- Selection is constrained on the pre-2022 development segment and must pass a separate 2022-2026 validation gate.",
        "- Costs are constant R estimates, not tick-by-tick broker fills.",
        "- Live V14.4 spread, staleness, daily-loss, and broker-expectancy guards remain required.",
        "- Backtests do not guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_results(payload: dict[str, Any]) -> None:
    import matplotlib.pyplot as plt

    labels = ["V14.5.1", "V14.5.2"]
    demo = [
        payload["results"]["ten_year/v14_5_1/demo_cost"]["net_profit"],
        payload["results"]["ten_year/v14_5_2/demo_cost"]["net_profit"],
    ]
    figure = plt.figure(figsize=(8, 5))
    bars = plt.bar(labels, demo)
    plt.axhline(0, linewidth=1)
    plt.ylabel("Net profit ($)")
    plt.title("Exact ten-year demo-cost profit")
    for bar, value in zip(bars, demo):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            f"${value:,.0f}",
            ha="center",
            va="bottom" if value >= 0 else "top",
        )
    plt.tight_layout()
    figure.savefig(OUT / "ten_year_demo_profit.png", dpi=170)
    plt.close(figure)

    yearly_static = payload["results"]["ten_year/v14_5_1/demo_cost"]["net_by_year"]
    yearly_new = payload["results"]["ten_year/v14_5_2/demo_cost"]["net_by_year"]
    years = sorted({int(key) for key in yearly_static} | {int(key) for key in yearly_new})
    cumulative_static = STARTING_BALANCE
    cumulative_new = STARTING_BALANCE
    static_equity = []
    new_equity = []
    for year in years:
        cumulative_static += float(yearly_static.get(str(year), 0.0))
        cumulative_new += float(yearly_new.get(str(year), 0.0))
        static_equity.append(cumulative_static)
        new_equity.append(cumulative_new)
    figure = plt.figure(figsize=(10, 6))
    plt.plot(years, static_equity, marker="o", label="V14.5.1")
    plt.plot(years, new_equity, marker="o", label="V14.5.2")
    plt.axhline(STARTING_BALANCE, linewidth=1)
    plt.xlabel("Exit year")
    plt.ylabel("Cumulative equity ($)")
    plt.title("Exact ten-year demo-cost equity")
    plt.legend()
    plt.tight_layout()
    figure.savefig(OUT / "ten_year_demo_equity.png", dpi=170)
    plt.close(figure)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = prepare_trade_stream()
    latest = max(item["exit"] for item in trades)
    ten_start = latest - timedelta(days=3652)
    selected, ranked, research = select_params(trades, ten_start, latest)

    results: dict[str, Any] = {}
    ledgers: dict[str, list[dict[str, Any]]] = {}
    for cost_name, costs in COSTS.items():
        static = replay(
            trades,
            "v14_5_1",
            costs,
            ten_start,
            latest,
            keep_ledger=True,
        )
        adaptive = replay(
            trades,
            "v14_5_2",
            costs,
            ten_start,
            latest,
            selected,
            keep_ledger=True,
        )
        ledgers[f"v14_5_1/{cost_name}"] = static.pop("ledger")
        ledgers[f"v14_5_2/{cost_name}"] = adaptive.pop("ledger")
        results[f"ten_year/v14_5_1/{cost_name}"] = static
        results[f"ten_year/v14_5_2/{cost_name}"] = adaptive

    payload = {
        "generated_at": datetime.now().isoformat(),
        "source_trade_count": len(trades),
        "ten_year_window": [str(ten_start), str(latest)],
        "promoted_engines": sorted(PROMOTED_V12_ENGINES),
        "preserved_constraints": {
            "max_trade_risk_percent": PROMOTED_RISK,
            "observation_risk_percent": OBSERVATION_RISK,
            "max_combined_open_risk_percent": MAX_OPEN_RISK,
            "drawdown_governor": GOVERNOR,
            "costs_r": COSTS,
        },
        "selected_params": asdict(selected),
        "selection_research": research,
        "top_candidates": ranked,
        "results": results,
    }
    (OUT / "v14_5_2_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    summary_rows = []
    for key, result in results.items():
        _, model, cost = key.split("/")
        summary_rows.append(
            {
                "model": model,
                "cost_scenario": cost,
                "trades": result["trades"],
                "ending_balance": result["ending_balance"],
                "net_profit": result["net_profit"],
                "return_percent": result["return_percent"],
                "profit_factor": result["profit_factor"],
                "max_drawdown_percent": result["max_drawdown_percent"],
                "stress_drawdown_percent": result["stress_drawdown_percent"],
                "win_rate_percent": result["win_rate_percent"],
            }
        )
    write_csv(
        OUT / "comparison_summary.csv",
        summary_rows,
        [
            "model",
            "cost_scenario",
            "trades",
            "ending_balance",
            "net_profit",
            "return_percent",
            "profit_factor",
            "max_drawdown_percent",
            "stress_drawdown_percent",
            "win_rate_percent",
        ],
    )
    write_csv(
        OUT / "top_candidates.csv",
        ranked,
        [
            "rank",
            "score",
            "development_net",
            "development_pf",
            "development_dd",
            "validation_net",
            "validation_pf",
            "validation_dd",
            "params",
        ],
    )
    for key, rows in ledgers.items():
        model, cost = key.split("/")
        write_csv(
            OUT / "ledgers" / f"{model}_{cost}.csv",
            rows,
            [
                "entry",
                "exit",
                "engine",
                "symbol",
                "setup",
                "group",
                "raw_r",
                "requested_risk_percent",
                "approved_risk_percent",
                "tier",
                "history_samples",
                "history_expectancy_r",
                "history_profit_factor",
                "net_r",
                "risk_dollars",
                "equity_at_entry",
                "drawdown_at_entry",
            ],
        )
    write_report(payload)
    plot_results(payload)

    print(json.dumps({
        "selected_params": asdict(selected),
        "development": {
            "v14_5_1": research["static_development"],
            "v14_5_2": research["selected_development"],
        },
        "validation": {
            "v14_5_1": research["static_validation"],
            "v14_5_2": research["selected_validation"],
        },
        "exact_ten_year_results": results,
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
