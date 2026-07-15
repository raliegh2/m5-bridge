"""Research-only replay for EURUSD, AUDUSD and USDJPY V12 enhancements."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from research.v14_3_production_improved_backtest import diagnostics, filter_window, load_ict_candidates, load_v12
from research.v14_3_profit_preserving_backtest import ResearchReplay

TARGET_SYMBOLS = ("EURUSD", "AUDUSD", "USDJPY")


def symbol_delta(current: dict[str, Any], enhanced: dict[str, Any], symbol: str) -> dict[str, float | int | None]:
    left = current["by_symbol"].get(symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0})
    right = enhanced["by_symbol"].get(symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0})
    return {
        "current_trades": int(left["trades"]),
        "enhanced_trades": int(right["trades"]),
        "current_net": float(left["net"]),
        "enhanced_net": float(right["net"]),
        "net_change": float(right["net"] - left["net"]),
        "current_profit_factor": left["profit_factor"],
        "enhanced_profit_factor": right["profit_factor"],
    }


def run_case(name: str, v12: pd.DataFrame, ict: pd.DataFrame, output: Path) -> dict[str, Any]:
    current_summary, current_trades, current_skipped = ResearchReplay(v12, ict).run()
    enhanced_v12 = apply_weak_symbol_profile(v12)
    enhanced_summary, enhanced_trades, enhanced_skipped = ResearchReplay(enhanced_v12, ict).run()

    case_dir = output / name
    case_dir.mkdir(parents=True, exist_ok=True)
    current_trades.to_csv(case_dir / "current_trades.csv", index=False)
    current_skipped.to_csv(case_dir / "current_skipped.csv", index=False)
    enhanced_trades.to_csv(case_dir / "enhanced_trades.csv", index=False)
    enhanced_skipped.to_csv(case_dir / "enhanced_skipped.csv", index=False)
    enhanced_v12.to_csv(case_dir / "enhanced_v12_ledger.csv", index=False)

    result = {
        "case": name,
        "current": current_summary,
        "enhanced": enhanced_summary,
        "difference": {
            "net_profit": enhanced_summary["net_profit"] - current_summary["net_profit"],
            "ending_balance": enhanced_summary["ending_balance"] - current_summary["ending_balance"],
            "profit_factor": enhanced_summary["profit_factor"] - current_summary["profit_factor"],
            "max_drawdown_points": enhanced_summary["max_closed_drawdown_percent"] - current_summary["max_closed_drawdown_percent"],
            "stress_drawdown_points": enhanced_summary["stress_drawdown_percent"] - current_summary["stress_drawdown_percent"],
            "trades": enhanced_summary["closed_trades"] - current_summary["closed_trades"],
        },
        "target_symbols": {symbol: symbol_delta(current_summary, enhanced_summary, symbol) for symbol in TARGET_SYMBOLS},
        "diagnostics": diagnostics(enhanced_v12, ict, enhanced_trades, enhanced_skipped),
    }
    (case_dir / "comparison.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def write_report(results: dict[str, Any], output: Path) -> None:
    lines = [
        "# V14.3 Weak-Symbol Enhancement Backtest",
        "",
        "The V12 signal engines remain enabled. The replay changes only pre-entry risk allocation for EURUSD, AUDUSD and USDJPY while retaining the profit-preserving V14.3 ICT profile.",
        "",
    ]
    for name, result in results.items():
        current = result["current"]
        enhanced = result["enhanced"]
        difference = result["difference"]
        lines.extend([
            f"## {name}",
            "",
            "| Portfolio metric | Current profit-preserving | Weak-symbol enhanced | Change |",
            "|---|---:|---:|---:|",
            f"| Net profit | ${current['net_profit']:,.2f} | ${enhanced['net_profit']:,.2f} | ${difference['net_profit']:,.2f} |",
            f"| Ending balance | ${current['ending_balance']:,.2f} | ${enhanced['ending_balance']:,.2f} | ${difference['ending_balance']:,.2f} |",
            f"| Profit factor | {current['profit_factor']:.3f} | {enhanced['profit_factor']:.3f} | {difference['profit_factor']:.3f} |",
            f"| Max closed DD | {current['max_closed_drawdown_percent']:.3f}% | {enhanced['max_closed_drawdown_percent']:.3f}% | {difference['max_drawdown_points']:.3f} pp |",
            f"| Stress DD | {current['stress_drawdown_percent']:.3f}% | {enhanced['stress_drawdown_percent']:.3f}% | {difference['stress_drawdown_points']:.3f} pp |",
            f"| Closed trades | {current['closed_trades']} | {enhanced['closed_trades']} | {difference['trades']} |",
            "",
            "### Target-symbol results",
            "",
            "| Symbol | Current net | Enhanced net | Change | Current PF | Enhanced PF |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        for symbol in TARGET_SYMBOLS:
            stats = result["target_symbols"][symbol]
            lines.append(
                f"| {symbol} | ${stats['current_net']:,.2f} | ${stats['enhanced_net']:,.2f} | ${stats['net_change']:,.2f} | "
                f"{stats['current_profit_factor']:.3f} | {stats['enhanced_profit_factor']:.3f} |"
            )
        lines.extend([
            "",
            "### Enhanced rejection codes",
            "",
            "```json",
            json.dumps(enhanced["skip_reasons"], indent=2),
            "```",
            "",
        ])
    lines.extend([
        "## Validation status",
        "",
        "Research/demo only. This is a ledger replay, not broker tick execution. The quality tiers must be shadow-tested before they are used for live sizing.",
    ])
    (output / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_results(results: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt

    ten = results["exact_10_year_window"]
    symbols = list(TARGET_SYMBOLS)
    current = [ten["target_symbols"][symbol]["current_net"] for symbol in symbols]
    enhanced = [ten["target_symbols"][symbol]["enhanced_net"] for symbol in symbols]
    positions = list(range(len(symbols)))
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.bar([position - 0.18 for position in positions], current, 0.36, label="Current")
    axis.bar([position + 0.18 for position in positions], enhanced, 0.36, label="Enhanced")
    axis.set_xticks(positions, symbols)
    axis.set_ylabel("Exact ten-year net profit ($)")
    axis.set_title("EURUSD, AUDUSD and USDJPY Enhancement")
    axis.axhline(0, linewidth=1)
    axis.legend()
    for container in axis.containers:
        axis.bar_label(container, fmt="$%.0f", padding=3)
    figure.tight_layout()
    figure.savefig(output / "weak_symbol_profit_comparison.png", dpi=160)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay weak-symbol V12 quality-risk enhancement")
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    v12 = load_v12(args.v12_ledger)
    ict = load_ict_candidates(args.ict_source)
    latest = max(v12["exit_time"].max(), ict["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    results = {
        "full_repository_history": run_case("full_repository_history", v12, ict, args.out),
        "exact_10_year_window": run_case(
            "exact_10_year_window",
            filter_window(v12, start, latest),
            filter_window(ict, start, latest),
            args.out,
        ),
    }
    (args.out / "all_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    write_report(results, args.out)
    plot_results(results, args.out)
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
