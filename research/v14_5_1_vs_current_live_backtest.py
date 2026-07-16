"""Cost-aware V14.5.1 versus current-live V14.3 comparison.

This replay uses the repository's combined V12 + ICT closed-trade stream and
compares two risk-allocation models over identical chronological windows:

* current_live_v14_3: risk recorded in the current combined ledger;
* v14_5_1: 0.75% for the promoted V12 engines in the committed V14.5.1
  profile and 0.025% observation risk for every other V12/ICT stream.

Both models use the same $5,000 starting balance, 1.75% ICT open-risk cap,
3.25% combined open-risk cap, drawdown governor and cost assumptions. The
replay produces machine-readable results, yearly profit/equity tables, trade
ledgers, a Markdown report and comparison charts.

This is an R-multiple replay, not a tick-level simulation. Constant costs are
modeled in R and V14.4 live-only guards that require broker observations
(spread at signal time, signal staleness and live expectancy history) cannot be
reconstructed from this historical ledger.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mt5_ai_bridge.v14_5_cost_robust_profile import (
    PROMOTED_V12_ENGINES,
    V14_5_OBSERVATION_RISK_PERCENT,
    V14_5_PROMOTED_RISK_PERCENT,
)

ROOT = Path(__file__).resolve().parents[1]
COMBINED = (
    ROOT
    / "research"
    / "v14_3_true_combined_v12_ict_output"
    / "true_combined_closed_trades.csv"
)
OUT_DIR = ROOT / "research" / "v14_5_1_vs_current_live_output"
STARTING_BALANCE = 5_000.0
MAX_ICT_OPEN_RISK = 1.75
MAX_COMBINED_OPEN_RISK = 3.25

COST_SCENARIOS = {
    "zero_cost": {"V12": 0.0, "ICT": 0.0},
    "demo_cost": {"V12": 0.02, "ICT": 0.075},
    "retail_cost": {"V12": 0.03, "ICT": 0.13},
}

# Same parity governor used by the live research-risk model. At the hard-stop
# tier this historical replay uses the observation floor, matching the existing
# V14.5 comparison script and avoiding a permanently frozen synthetic account.
GOVERNOR = ((9.6, 0.0), (9.0, 0.50), (8.5, 0.82), (7.5, 0.98))
MODELS = ("current_live_v14_3", "v14_5_1")


@dataclass
class OpenRisk:
    exit_time: datetime
    risk_percent: float
    engine_group: str


def governed_risk(risk: float, drawdown_percent: float) -> float:
    for threshold, multiplier in GOVERNOR:
        if drawdown_percent >= threshold:
            if multiplier <= 0:
                return min(risk, V14_5_OBSERVATION_RISK_PERCENT)
            return risk * multiplier
    return risk


def load_trades() -> list[dict[str, Any]]:
    with COMBINED.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    trades: list[dict[str, Any]] = []
    for row in rows:
        trades.append(
            {
                "trade_id": int(row["trade_id"]),
                "engine_group": row["engine_group"].upper(),
                "engine": row["engine"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "side": row.get("side", ""),
                "entry": datetime.fromisoformat(row["entry_time"]),
                "exit": datetime.fromisoformat(row["exit_time"]),
                "r": float(row["r_multiple"]),
                "ledger_risk": float(row["risk_percent"]),
            }
        )
    trades.sort(key=lambda item: (item["entry"], item["exit"], item["trade_id"]))
    return trades


def model_risk(trade: dict[str, Any], model: str) -> float:
    if model == "current_live_v14_3":
        return float(trade["ledger_risk"])
    if model == "v14_5_1":
        if (
            trade["engine_group"] == "V12"
            and trade["engine"] in PROMOTED_V12_ENGINES
        ):
            return float(V14_5_PROMOTED_RISK_PERCENT)
        return float(V14_5_OBSERVATION_RISK_PERCENT)
    raise ValueError(f"Unknown model: {model}")


def replay(
    trades: list[dict[str, Any]],
    model: str,
    costs: dict[str, float],
    window_start: datetime | None,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    equity = STARTING_BALANCE
    peak = equity
    max_dd = 0.0
    max_stress_dd = 0.0
    open_positions: list[OpenRisk] = []
    closed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    first_entry: datetime | None = None
    last_exit: datetime | None = None
    yearly: dict[int, dict[str, float | int]] = {}
    by_engine: dict[str, dict[str, float | int]] = {}
    by_symbol: dict[str, dict[str, float | int]] = {}

    for trade in trades:
        if window_start is not None and trade["entry"] < window_start:
            continue
        if window_end is not None and trade["entry"] > window_end:
            continue

        open_positions = [
            position
            for position in open_positions
            if position.exit_time > trade["entry"]
        ]
        requested = model_risk(trade, model)
        if requested <= 0:
            continue

        drawdown_before = (
            max(0.0, (peak - equity) / peak * 100.0) if peak > 0 else 0.0
        )
        approved = governed_risk(requested, drawdown_before)
        combined_open = sum(position.risk_percent for position in open_positions)
        ict_open = sum(
            position.risk_percent
            for position in open_positions
            if position.engine_group == "ICT"
        )
        if (
            trade["engine_group"] == "ICT"
            and ict_open + approved > MAX_ICT_OPEN_RISK + 1e-12
        ):
            skipped.append({**trade, "reason": "ICT_OPEN_RISK_CAP"})
            continue
        if combined_open + approved > MAX_COMBINED_OPEN_RISK + 1e-12:
            skipped.append({**trade, "reason": "COMBINED_OPEN_RISK_CAP"})
            continue

        open_positions.append(
            OpenRisk(trade["exit"], approved, trade["engine_group"])
        )
        stressed_equity = equity - equity * sum(
            position.risk_percent for position in open_positions
        ) / 100.0
        max_stress_dd = max(
            max_stress_dd,
            (peak - stressed_equity) / peak * 100.0 if peak > 0 else 0.0,
        )

        cost_r = float(costs[trade["engine_group"]])
        net_r = float(trade["r"]) - cost_r
        risk_dollars = equity * approved / 100.0
        pnl = risk_dollars * net_r
        equity_before = equity
        equity += pnl
        peak = max(peak, equity)
        drawdown_after = (
            max(0.0, (peak - equity) / peak * 100.0) if peak > 0 else 0.0
        )
        max_dd = max(max_dd, drawdown_after)

        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss -= pnl
        wins += int(net_r > 0)
        first_entry = first_entry or trade["entry"]
        last_exit = trade["exit"] if last_exit is None else max(last_exit, trade["exit"])

        year = trade["exit"].year
        year_item = yearly.setdefault(
            year,
            {"trades": 0, "net_profit": 0.0, "ending_equity": equity_before},
        )
        year_item["trades"] = int(year_item["trades"]) + 1
        year_item["net_profit"] = float(year_item["net_profit"]) + pnl
        year_item["ending_equity"] = equity

        for key, container in (
            (trade["engine"], by_engine),
            (trade["symbol"], by_symbol),
        ):
            item = container.setdefault(
                key,
                {"trades": 0, "net_profit": 0.0, "gross_profit": 0.0, "gross_loss": 0.0},
            )
            item["trades"] = int(item["trades"]) + 1
            item["net_profit"] = float(item["net_profit"]) + pnl
            if pnl >= 0:
                item["gross_profit"] = float(item["gross_profit"]) + pnl
            else:
                item["gross_loss"] = float(item["gross_loss"]) - pnl

        closed.append(
            {
                **trade,
                "requested_risk_percent": requested,
                "approved_risk_percent": approved,
                "cost_r": cost_r,
                "net_r": net_r,
                "risk_dollars": risk_dollars,
                "pnl": pnl,
                "equity_before": equity_before,
                "equity_after": equity,
                "drawdown_after_percent": drawdown_after,
            }
        )

    years = (
        max((last_exit - first_entry).days / 365.25, 1e-9)
        if first_entry is not None and last_exit is not None
        else 0.0
    )

    def attribution(values: dict[str, dict[str, float | int]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in values.items():
            loss = float(value["gross_loss"])
            profit = float(value["gross_profit"])
            output[key] = {
                "trades": int(value["trades"]),
                "net_profit": round(float(value["net_profit"]), 2),
                "profit_factor": round(profit / loss, 4) if loss > 0 else None,
            }
        return dict(
            sorted(
                output.items(),
                key=lambda item: item[1]["net_profit"],
                reverse=True,
            )
        )

    return {
        "model": model,
        "trades": len(closed),
        "skipped": len(skipped),
        "starting_balance": STARTING_BALANCE,
        "ending_balance": round(equity, 2),
        "net_profit": round(equity - STARTING_BALANCE, 2),
        "return_percent": round((equity / STARTING_BALANCE - 1.0) * 100.0, 2),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "win_rate_percent": round(wins / len(closed) * 100.0, 2) if closed else None,
        "max_drawdown_percent": round(max_dd, 4),
        "stress_drawdown_percent": round(max_stress_dd, 4),
        "years": round(years, 2),
        "cagr_percent": (
            round(((equity / STARTING_BALANCE) ** (1.0 / years) - 1.0) * 100.0, 2)
            if years > 0 and equity > 0
            else None
        ),
        "first_entry": first_entry.isoformat() if first_entry else None,
        "last_exit": last_exit.isoformat() if last_exit else None,
        "net_by_year": {
            str(year): {
                "trades": int(values["trades"]),
                "net_profit": round(float(values["net_profit"]), 2),
                "ending_equity": round(float(values["ending_equity"]), 2),
            }
            for year, values in sorted(yearly.items())
        },
        "by_engine": attribution(by_engine),
        "by_symbol": attribution(by_symbol),
        "closed_trades": closed,
        "skipped_trades": skipped,
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def serialize_trade(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    for key in ("entry", "exit"):
        if isinstance(output.get(key), datetime):
            output[key] = output[key].isoformat()
    return output


def plot_results(summary_rows: list[dict[str, Any]], yearly_rows: list[dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt

    ten_demo = [
        row
        for row in summary_rows
        if row["window"] == "exact_ten_year" and row["cost_scenario"] == "demo_cost"
    ]
    figure = plt.figure(figsize=(8, 5))
    labels = ["Current live V14.3" if row["model"] == "current_live_v14_3" else "V14.5.1" for row in ten_demo]
    values = [float(row["net_profit"]) for row in ten_demo]
    bars = plt.bar(labels, values)
    plt.axhline(0, linewidth=1)
    plt.ylabel("Net profit ($)")
    plt.title("Exact 10-year profit — demo-cost assumption")
    for bar, value in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2, value, f"${value:,.0f}", ha="center", va="bottom" if value >= 0 else "top")
    plt.tight_layout()
    figure.savefig(OUT_DIR / "exact_10_year_demo_profit.png", dpi=170)
    plt.close(figure)

    figure = plt.figure(figsize=(10, 6))
    for model in MODELS:
        rows = [
            row
            for row in yearly_rows
            if row["window"] == "exact_ten_year"
            and row["cost_scenario"] == "demo_cost"
            and row["model"] == model
        ]
        rows.sort(key=lambda item: int(item["year"]))
        label = "Current live V14.3" if model == "current_live_v14_3" else "V14.5.1"
        plt.plot(
            [int(row["year"]) for row in rows],
            [float(row["ending_equity"]) for row in rows],
            marker="o",
            label=label,
        )
    plt.axhline(STARTING_BALANCE, linewidth=1)
    plt.xlabel("Exit year")
    plt.ylabel("Ending equity ($)")
    plt.title("Exact 10-year equity by year — demo-cost assumption")
    plt.legend()
    plt.tight_layout()
    figure.savefig(OUT_DIR / "exact_10_year_demo_equity_by_year.png", dpi=170)
    plt.close(figure)


def write_report(payload: dict[str, Any], summary_rows: list[dict[str, Any]], yearly_rows: list[dict[str, Any]]) -> None:
    exact_start = payload["windows"]["exact_ten_year"]["start"][:10]
    exact_end = payload["windows"]["exact_ten_year"]["end"][:10]
    lines = [
        "# V14.5.1 vs Current Live V14.3 Backtest",
        "",
        f"**Common exact-ten-year period:** {exact_start} to {exact_end}",
        f"**Starting balance:** ${STARTING_BALANCE:,.2f}",
        "",
        "V14.5.1 uses the committed July 2026 profile: 0.75% risk for GBPUSD_V10_PRECISION, GBPJPY_SWING_CORE and EURUSD_SWING_CORE; AUDUSD_TREND_PULLBACK and every other V12/ICT stream run at 0.025% observation risk.",
        "",
        "## Exact ten-year results",
        "",
        "| Cost assumption | Model | Trades | Net profit | Ending balance | Return | PF | Max DD | Stress DD | CAGR |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cost_name in COST_SCENARIOS:
        for model in MODELS:
            row = next(
                item
                for item in summary_rows
                if item["window"] == "exact_ten_year"
                and item["cost_scenario"] == cost_name
                and item["model"] == model
            )
            model_label = "Current live V14.3" if model == "current_live_v14_3" else "V14.5.1"
            lines.append(
                f"| {cost_name} | {model_label} | {row['trades']} | ${row['net_profit']:,.2f} | "
                f"${row['ending_balance']:,.2f} | {row['return_percent']:.2f}% | "
                f"{float(row['profit_factor'] or 0):.4f} | {row['max_drawdown_percent']:.4f}% | "
                f"{row['stress_drawdown_percent']:.4f}% | {float(row['cagr_percent'] or 0):.2f}% |"
            )

    lines += [
        "",
        "## Demo-cost profit by year",
        "",
        "| Year | Current live V14.3 profit | V14.5.1 profit | Current ending equity | V14.5.1 ending equity |",
        "|---:|---:|---:|---:|---:|",
    ]
    years = sorted({
        int(row["year"])
        for row in yearly_rows
        if row["window"] == "exact_ten_year" and row["cost_scenario"] == "demo_cost"
    })
    for year in years:
        current = next(
            (row for row in yearly_rows if row["window"] == "exact_ten_year" and row["cost_scenario"] == "demo_cost" and row["model"] == "current_live_v14_3" and int(row["year"]) == year),
            {"net_profit": 0.0, "ending_equity": STARTING_BALANCE},
        )
        new = next(
            (row for row in yearly_rows if row["window"] == "exact_ten_year" and row["cost_scenario"] == "demo_cost" and row["model"] == "v14_5_1" and int(row["year"]) == year),
            {"net_profit": 0.0, "ending_equity": STARTING_BALANCE},
        )
        lines.append(
            f"| {year} | ${float(current['net_profit']):,.2f} | ${float(new['net_profit']):,.2f} | "
            f"${float(current['ending_equity']):,.2f} | ${float(new['ending_equity']):,.2f} |"
        )

    lines += [
        "",
        "## Interpretation boundaries",
        "",
        "- The current-live column is the current combined-ledger allocation replayed under the same cost and capacity assumptions as V14.5.1.",
        "- The exact-ten-year period is determined from the latest exit in the repository ledger; V12 swing history ends in March 2022, while the GBP ICT stream continues through July 2026.",
        "- V14.4 live-only spread, staleness, daily-loss and rolling-expectancy guards require broker observations and are not reconstructable from this closed-trade ledger.",
        "- Results are simulated and do not guarantee future or broker-native profitability.",
    ]
    (OUT_DIR / "BACKTEST_COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    trades = load_trades()
    latest_exit = max(item["exit"] for item in trades)
    earliest_entry = min(item["entry"] for item in trades)
    ten_year_start = latest_exit - timedelta(days=3652)
    swing_end = max(
        item["exit"] for item in trades if item["engine_group"] == "V12"
    )
    windows = {
        "full_history": (None, latest_exit),
        "exact_ten_year": (ten_year_start, latest_exit),
        "ten_year_swing_covered": (ten_year_start, swing_end),
        "post_swing_history": (swing_end, latest_exit),
    }

    results: dict[str, Any] = {}
    summary_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []

    for window_name, (window_start, window_end) in windows.items():
        for cost_name, costs in COST_SCENARIOS.items():
            for model in MODELS:
                result = replay(trades, model, costs, window_start, window_end)
                key = f"{window_name}/{model}/{cost_name}"
                closed = result.pop("closed_trades")
                skipped = result.pop("skipped_trades")
                results[key] = result
                summary_rows.append(
                    {
                        "window": window_name,
                        "cost_scenario": cost_name,
                        **result,
                    }
                )
                for year, values in result["net_by_year"].items():
                    yearly_rows.append(
                        {
                            "window": window_name,
                            "cost_scenario": cost_name,
                            "model": model,
                            "year": int(year),
                            **values,
                        }
                    )
                trade_fields = [
                    "trade_id", "engine_group", "engine", "symbol", "setup", "side",
                    "entry", "exit", "r", "ledger_risk", "requested_risk_percent",
                    "approved_risk_percent", "cost_r", "net_r", "risk_dollars", "pnl",
                    "equity_before", "equity_after", "drawdown_after_percent",
                ]
                write_csv(
                    OUT_DIR / "trades" / f"{window_name}__{model}__{cost_name}.csv",
                    [serialize_trade(item) for item in closed],
                    trade_fields,
                )
                write_csv(
                    OUT_DIR / "skipped" / f"{window_name}__{model}__{cost_name}.csv",
                    [serialize_trade(item) for item in skipped],
                    [
                        "trade_id", "engine_group", "engine", "symbol", "setup", "side",
                        "entry", "exit", "r", "ledger_risk", "reason",
                    ],
                )

    payload = {
        "generated_at": datetime.now().isoformat(),
        "source": str(COMBINED.relative_to(ROOT)),
        "source_trade_count": len(trades),
        "source_span": {
            "start": earliest_entry.isoformat(),
            "end": latest_exit.isoformat(),
        },
        "starting_balance": STARTING_BALANCE,
        "models": {
            "current_live_v14_3": "risk_percent recorded in the current combined ledger",
            "v14_5_1": {
                "promoted_v12_engines": sorted(PROMOTED_V12_ENGINES),
                "promoted_risk_percent": V14_5_PROMOTED_RISK_PERCENT,
                "observation_risk_percent": V14_5_OBSERVATION_RISK_PERCENT,
            },
        },
        "limits": {
            "max_ict_open_risk_percent": MAX_ICT_OPEN_RISK,
            "max_combined_open_risk_percent": MAX_COMBINED_OPEN_RISK,
            "governor": GOVERNOR,
        },
        "cost_scenarios_r": COST_SCENARIOS,
        "windows": {
            name: {
                "start": (start or earliest_entry).isoformat(),
                "end": end.isoformat(),
            }
            for name, (start, end) in windows.items()
        },
        "results": results,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "comparison_results.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    summary_fields = [
        "window", "cost_scenario", "model", "trades", "skipped",
        "starting_balance", "ending_balance", "net_profit", "return_percent",
        "profit_factor", "win_rate_percent", "max_drawdown_percent",
        "stress_drawdown_percent", "years", "cagr_percent", "first_entry", "last_exit",
    ]
    write_csv(OUT_DIR / "comparison_summary.csv", summary_rows, summary_fields)
    write_csv(
        OUT_DIR / "yearly_profit_equity.csv",
        yearly_rows,
        ["window", "cost_scenario", "model", "year", "trades", "net_profit", "ending_equity"],
    )
    write_report(payload, summary_rows, yearly_rows)
    plot_results(summary_rows, yearly_rows)

    print(json.dumps({
        "exact_ten_year_period": payload["windows"]["exact_ten_year"],
        "v14_5_1_promoted_engines": sorted(PROMOTED_V12_ENGINES),
        "exact_ten_year_results": {
            key: value
            for key, value in results.items()
            if key.startswith("exact_ten_year/")
        },
        "output": str(OUT_DIR),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
