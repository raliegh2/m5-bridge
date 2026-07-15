"""Backtest the drawdown-limited V12 + V14.3 five-symbol portfolio.

The signal candidates are identical to the verified five-symbol ICT replay. Only
pre-entry risk allocation and the equity-state governor change. This module is
research-only and never connects to a broker.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_drawdown_governor import (
    CONFIG,
    ICT_NORMAL_MULTIPLIER,
    MAX_ICT_TRADE_RISK_PERCENT,
    MAX_V12_TRADE_RISK_PERCENT,
    V12_ENGINE_MULTIPLIERS,
    DrawdownGovernorState,
    adjusted_ict_risk_percent,
    adjusted_v12_risk_percent,
)
from mt5_ai_bridge.v14_3_profit_preserving_profile import (
    PORTFOLIO_GUARD,
    SETUP_RISK_PERCENT,
    SYMBOL_GUARDS,
)
from research.v14_3_five_symbol_ict_10y_backtest import (
    ALL_SYMBOLS,
    build_new_ict_candidates,
    install_all_symbol_ict_profile,
    mode_coverage,
    source_attribution,
)
from research.v14_3_production_improved_backtest import (
    filter_window,
    load_ict_candidates,
    load_v12,
    summarize,
)
from research.v14_3_profit_preserving_backtest import ResearchReplay


class DrawdownLimitedReplay(ResearchReplay):
    """Research replay with the frozen portfolio drawdown governor."""

    def __init__(self, v12: pd.DataFrame, ict: pd.DataFrame) -> None:
        super().__init__(v12, ict)
        self.governor = DrawdownGovernorState()

    def run(self) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
        stream = [(row["entry_time"], "V12", row) for row in self.v12.to_dict("records")]
        stream += [(row["entry_time"], "ICT", row) for row in self.ict.to_dict("records")]
        stream.sort(key=lambda item: (item[0], 0 if item[1] == "V12" else 1))

        for now, group, row in stream:
            self.close_due(now)
            self.reset_day(now)
            self.governor.observe(now, self.dd())

            if group == "ICT" and self.governor.in_pause(now):
                self.skipped.append(
                    {
                        **row,
                        "skip_reason": "EQUITY_DRAWDOWN_PAUSE",
                        "governor_phase": self.governor.phase(now),
                        "pre_entry_drawdown_percent": self.dd(),
                    }
                )
                continue

            total_open = sum(item["risk_percent"] for item in self.active)
            ict_open = sum(
                item["risk_percent"]
                for item in self.active
                if item["engine_group"] == "ICT"
            )

            if group == "V12":
                risk, tier = adjusted_v12_risk_percent(
                    row["engine"], row["risk_percent"]
                )
                admission_reason = tier
            else:
                blocked = self.reject_reason(row, now)
                if blocked:
                    self.skipped.append(
                        {
                            **row,
                            "skip_reason": blocked,
                            "governor_phase": self.governor.phase(now),
                            "pre_entry_drawdown_percent": self.dd(),
                        }
                    )
                    continue

                symbol = row["symbol"]
                pressure = (
                    self.day.global_consecutive_losses > 0
                    or self.day.loss_pressure[symbol] > 0
                    or self.day.daily_pnl[symbol] < 0
                )
                recovery_multiplier = self.governor.recovery_multiplier(now)
                risk = adjusted_ict_risk_percent(
                    symbol,
                    row["setup"],
                    self.dd(),
                    pressure,
                    recovery_multiplier,
                )
                if ict_open + risk > PORTFOLIO_GUARD.max_ict_open_risk_percent + 1e-12:
                    self.skipped.append(
                        {
                            **row,
                            "skip_reason": "ICT_OPEN_RISK_CAP",
                            "governor_phase": self.governor.phase(now),
                            "pre_entry_drawdown_percent": self.dd(),
                        }
                    )
                    continue
                if total_open + risk > PORTFOLIO_GUARD.max_combined_open_risk_percent + 1e-12:
                    self.skipped.append(
                        {
                            **row,
                            "skip_reason": "COMBINED_OPEN_RISK_CAP",
                            "governor_phase": self.governor.phase(now),
                            "pre_entry_drawdown_percent": self.dd(),
                        }
                    )
                    continue
                admission_reason = f"ICT_DRAWDOWN_{self.governor.phase(now)}"

            item = {
                "trade_id": self.trade_id,
                "engine_group": group,
                "engine": row["engine"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "side": row.get("side", ""),
                "entry_time": now,
                "exit_time": row["exit_time"],
                "risk_percent": risk,
                "risk_dollars": self.balance * risk / 100.0,
                "r_multiple": float(row["r_multiple"]),
                "admission_reason": admission_reason,
                "governor_phase": self.governor.phase(now),
                "pre_entry_drawdown_percent": self.dd(),
            }
            self.trade_id += 1
            self.active.append(item)
            if group == "ICT":
                self.day.total_entries.append(now)
                self.day.entries[item["symbol"]].append(now)

            stressed = self.balance - sum(
                active_item["risk_dollars"] for active_item in self.active
            )
            self.stress_dd = max(
                self.stress_dd,
                (self.peak - stressed) / self.peak * 100.0,
            )

        self.close_due(pd.Timestamp.max.tz_localize("UTC"))
        summary = summarize(
            PORTFOLIO_GUARD.starting_balance,
            self.balance,
            self.max_dd,
            self.stress_dd,
            self.closed,
            self.skipped,
        )
        summary["profile"] = {
            "setup_risk_percent": {
                f"{symbol}/{setup}": risk
                for (symbol, setup), risk in SETUP_RISK_PERCENT.items()
            },
            "portfolio_guard": PORTFOLIO_GUARD.__dict__,
            "symbol_guards": {
                symbol: guard.__dict__ for symbol, guard in SYMBOL_GUARDS.items()
            },
            "drawdown_governor": asdict(CONFIG),
            "v12_engine_multipliers": V12_ENGINE_MULTIPLIERS,
            "ict_normal_multiplier": ICT_NORMAL_MULTIPLIER,
            "max_v12_trade_risk_percent": MAX_V12_TRADE_RISK_PERCENT,
            "max_ict_trade_risk_percent": MAX_ICT_TRADE_RISK_PERCENT,
            "governor_trigger_count": self.governor.trigger_count,
        }
        return summary, pd.DataFrame(self.closed), pd.DataFrame(self.skipped)


def combine_ict(existing_ict: pd.DataFrame, new_ict: pd.DataFrame) -> pd.DataFrame:
    all_ict = pd.concat([existing_ict, new_ict], ignore_index=True, sort=False)
    return (
        all_ict.sort_values(["entry_time", "symbol", "engine"])
        .drop_duplicates(["entry_time", "exit_time", "symbol", "engine", "side"])
        .reset_index(drop=True)
    )


def run_case(
    name: str,
    v12: pd.DataFrame,
    all_ict: pd.DataFrame,
    output: Path,
) -> dict[str, Any]:
    current_summary, current_trades, current_skipped = ResearchReplay(v12, all_ict).run()
    improved_summary, improved_trades, improved_skipped = DrawdownLimitedReplay(
        v12, all_ict
    ).run()

    folder = output / name
    folder.mkdir(parents=True, exist_ok=True)
    current_trades.to_csv(folder / "current_trades.csv", index=False)
    current_skipped.to_csv(folder / "current_skipped.csv", index=False)
    improved_trades.to_csv(folder / "drawdown_limited_trades.csv", index=False)
    improved_skipped.to_csv(folder / "drawdown_limited_skipped.csv", index=False)
    all_ict.to_csv(folder / "all_ict_candidates.csv", index=False)

    result = {
        "case": name,
        "current_five_symbol": current_summary,
        "drawdown_limited": improved_summary,
        "difference": {
            "net_profit": improved_summary["net_profit"] - current_summary["net_profit"],
            "ending_balance": improved_summary["ending_balance"] - current_summary["ending_balance"],
            "profit_factor": improved_summary["profit_factor"] - current_summary["profit_factor"],
            "max_drawdown_points": improved_summary["max_closed_drawdown_percent"]
            - current_summary["max_closed_drawdown_percent"],
            "stress_drawdown_points": improved_summary["stress_drawdown_percent"]
            - current_summary["stress_drawdown_percent"],
            "closed_trades": improved_summary["closed_trades"] - current_summary["closed_trades"],
        },
        "mode_coverage": mode_coverage(v12, all_ict, improved_trades),
        "engine_attribution": source_attribution(improved_trades),
    }
    (folder / "comparison.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    return result


def write_report(results: dict[str, Any], output: Path) -> None:
    lines = [
        "# V14.3 Drawdown-Limited Five-Symbol Backtest",
        "",
        "The candidate stream is unchanged. The improved replay adds a 6% equity-drawdown trigger, a 72-hour ICT pause, 30% ICT recovery sizing until drawdown falls below 4%, a 5% bounded ICT normal-state uplift and a 1.50x bounded allocation for four validated V12 engines.",
        "",
    ]
    for name, result in results.items():
        current = result["current_five_symbol"]
        improved = result["drawdown_limited"]
        difference = result["difference"]
        lines.extend(
            [
                f"## {name}",
                "",
                "| Metric | Current five-symbol | Drawdown-limited | Change |",
                "|---|---:|---:|---:|",
                f"| Net profit | ${current['net_profit']:,.2f} | ${improved['net_profit']:,.2f} | ${difference['net_profit']:,.2f} |",
                f"| Ending balance | ${current['ending_balance']:,.2f} | ${improved['ending_balance']:,.2f} | ${difference['ending_balance']:,.2f} |",
                f"| Profit factor | {current['profit_factor']:.4f} | {improved['profit_factor']:.4f} | {difference['profit_factor']:.4f} |",
                f"| Max closed DD | {current['max_closed_drawdown_percent']:.4f}% | {improved['max_closed_drawdown_percent']:.4f}% | {difference['max_drawdown_points']:.4f} pp |",
                f"| Stress DD | {current['stress_drawdown_percent']:.4f}% | {improved['stress_drawdown_percent']:.4f}% | {difference['stress_drawdown_points']:.4f} pp |",
                f"| Closed trades | {current['closed_trades']} | {improved['closed_trades']} | {difference['closed_trades']} |",
                "",
                "### Drawdown-limited profit by symbol",
                "",
                "| Symbol | Trades | Net profit | PF | V12 candidates | ICT candidates |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for symbol in ALL_SYMBOLS:
            stats = improved["by_symbol"].get(
                symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0}
            )
            coverage = result["mode_coverage"][symbol]
            lines.append(
                f"| {symbol} | {stats['trades']} | ${stats['net']:,.2f} | "
                f"{stats['profit_factor']:.3f} | {coverage['v12_candidates']} | "
                f"{coverage['ict_candidates']} |"
            )
        lines.extend(
            [
                "",
                "### Governor activity",
                "",
                f"- Triggers: {improved['profile']['governor_trigger_count']}",
                f"- ICT pause rejections: {improved['skip_reasons'].get('EQUITY_DRAWDOWN_PAUSE', 0)}",
                "",
            ]
        )
    lines.extend(
        [
            "## Status",
            "",
            "Research/demo only. Historical profitability and drawdown do not include broker-native spread, commission, swap, slippage or forward execution uncertainty.",
        ]
    )
    (output / "BACKTEST_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def plot_results(results: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt

    labels = list(results)
    current_profit = [
        results[name]["current_five_symbol"]["net_profit"] for name in labels
    ]
    improved_profit = [
        results[name]["drawdown_limited"]["net_profit"] for name in labels
    ]
    positions = list(range(len(labels)))
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.bar(
        [position - 0.18 for position in positions],
        current_profit,
        0.36,
        label="Current",
    )
    axis.bar(
        [position + 0.18 for position in positions],
        improved_profit,
        0.36,
        label="Drawdown-limited",
    )
    axis.set_xticks(positions, labels)
    axis.set_ylabel("Net profit ($)")
    axis.set_title("Drawdown-Limited Profit Comparison")
    axis.legend()
    for container in axis.containers:
        axis.bar_label(container, fmt="$%.0f", padding=3)
    figure.tight_layout()
    figure.savefig(output / "profit_comparison.png", dpi=160)
    plt.close(figure)

    current_dd = [
        results[name]["current_five_symbol"]["max_closed_drawdown_percent"]
        for name in labels
    ]
    improved_dd = [
        results[name]["drawdown_limited"]["max_closed_drawdown_percent"]
        for name in labels
    ]
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.bar(
        [position - 0.18 for position in positions], current_dd, 0.36, label="Current"
    )
    axis.bar(
        [position + 0.18 for position in positions],
        improved_dd,
        0.36,
        label="Drawdown-limited",
    )
    axis.set_xticks(positions, labels)
    axis.set_ylabel("Max closed drawdown (%)")
    axis.set_title("Drawdown Comparison")
    axis.legend()
    for container in axis.containers:
        axis.bar_label(container, fmt="%.2f%%", padding=3)
    figure.tight_layout()
    figure.savefig(output / "drawdown_comparison.png", dpi=160)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run drawdown-limited five-symbol replay")
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    install_all_symbol_ict_profile()
    v12 = apply_weak_symbol_profile(load_v12(args.v12_ledger))
    existing_ict = load_ict_candidates(args.ict_source)
    new_ict, selection = build_new_ict_candidates(args.out)
    all_ict = combine_ict(existing_ict, new_ict)

    latest = max(v12["exit_time"].max(), all_ict["exit_time"].max())
    ten_year_start = latest - pd.DateOffset(years=10)
    results = {
        "full_repository_history": run_case(
            "full_repository_history", v12, all_ict, args.out
        ),
        "exact_10_year_window": run_case(
            "exact_10_year_window",
            filter_window(v12, ten_year_start, latest),
            filter_window(all_ict, ten_year_start, latest),
            args.out,
        ),
    }
    payload = {
        "selection": selection,
        "governor": {
            "config": asdict(CONFIG),
            "v12_engine_multipliers": V12_ENGINE_MULTIPLIERS,
            "ict_normal_multiplier": ICT_NORMAL_MULTIPLIER,
            "max_v12_trade_risk_percent": MAX_V12_TRADE_RISK_PERCENT,
            "max_ict_trade_risk_percent": MAX_ICT_TRADE_RISK_PERCENT,
        },
        "results": results,
    }
    (args.out / "all_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(results, args.out)
    plot_results(results, args.out)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
