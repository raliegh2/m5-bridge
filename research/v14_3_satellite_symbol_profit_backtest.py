"""Ten-year replay for materially stronger EURUSD, AUDUSD and USDJPY sleeves.

Research only. No MT5 connection or order placement is included.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor
from mt5_ai_bridge.v14_3_satellite_symbol_profile import (
    TARGET_SYMBOLS,
    apply_satellite_v12_risk,
    filter_satellite_ict,
    install_satellite_ict_risk,
)
from research.v14_3_drawdown_limited_backtest_v2 import AdmissionPreservingReplay
from research.v14_3_five_symbol_ict_10y_backtest import (
    ALL_SYMBOLS,
    build_new_ict_candidates,
    install_all_symbol_ict_profile,
    mode_coverage,
    source_attribution,
)
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates, load_v12


def combine_ict(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    frame = pd.concat([existing, new], ignore_index=True, sort=False)
    return frame.sort_values(["entry_time", "symbol", "engine"]).drop_duplicates(
        ["entry_time", "exit_time", "symbol", "engine", "side"]
    ).reset_index(drop=True)


def run_replay(v12: pd.DataFrame, ict: pd.DataFrame, governor: DrawdownGovernor):
    replay = AdmissionPreservingReplay(v12, ict, governor)
    summary, trades, skipped = replay.run()
    return summary, trades, skipped, pd.DataFrame(replay.governor_events)


def target_symbol_summary(summary: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    total = 0.0
    for symbol in TARGET_SYMBOLS:
        stats = summary["by_symbol"].get(symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0})
        result[symbol] = stats
        total += float(stats["net"])
    result["combined_net"] = total
    return result


def validation_windows(frame: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for symbol in TARGET_SYMBOLS:
        group = frame[frame["symbol"] == symbol].copy().sort_values("entry_time")
        n = len(group)
        cuts = (int(n * 0.50), int(n * 0.75))
        windows = {
            "development": group.iloc[: cuts[0]],
            "confirmation": group.iloc[cuts[0] : cuts[1]],
            "validation": group.iloc[cuts[1] :],
        }
        stats: dict[str, Any] = {"candidates": n}
        for name, section in windows.items():
            r = section["r_multiple"].astype(float) if not section.empty else pd.Series(dtype=float)
            gross_profit = float(r[r > 0].sum())
            gross_loss = float(-r[r < 0].sum())
            stats[name] = {
                "trades": int(len(section)),
                "net_r": float(r.sum()),
                "profit_factor": gross_profit / gross_loss if gross_loss else (None if gross_profit == 0 else float("inf")),
            }
        output[symbol] = stats
    return output


def run_case(
    name: str,
    baseline_v12: pd.DataFrame,
    baseline_ict: pd.DataFrame,
    enhanced_v12: pd.DataFrame,
    enhanced_ict: pd.DataFrame,
    governor: DrawdownGovernor,
    output: Path,
) -> dict[str, Any]:
    baseline, baseline_trades, baseline_skipped, baseline_governor = run_replay(
        baseline_v12, baseline_ict, governor
    )
    enhanced, enhanced_trades, enhanced_skipped, enhanced_governor = run_replay(
        enhanced_v12, enhanced_ict, governor
    )

    folder = output / name
    folder.mkdir(parents=True, exist_ok=True)
    baseline_trades.to_csv(folder / "baseline_trades.csv", index=False)
    baseline_skipped.to_csv(folder / "baseline_skipped.csv", index=False)
    baseline_governor.to_csv(folder / "baseline_governor.csv", index=False)
    enhanced_trades.to_csv(folder / "enhanced_trades.csv", index=False)
    enhanced_skipped.to_csv(folder / "enhanced_skipped.csv", index=False)
    enhanced_governor.to_csv(folder / "enhanced_governor.csv", index=False)

    result = {
        "case": name,
        "baseline": baseline,
        "enhanced": enhanced,
        "difference": {
            "net_profit": enhanced["net_profit"] - baseline["net_profit"],
            "ending_balance": enhanced["ending_balance"] - baseline["ending_balance"],
            "profit_factor": enhanced["profit_factor"] - baseline["profit_factor"],
            "max_drawdown_points": enhanced["max_closed_drawdown_percent"] - baseline["max_closed_drawdown_percent"],
            "stress_drawdown_points": enhanced["stress_drawdown_percent"] - baseline["stress_drawdown_percent"],
            "closed_trades": enhanced["closed_trades"] - baseline["closed_trades"],
        },
        "baseline_target_symbols": target_symbol_summary(baseline),
        "enhanced_target_symbols": target_symbol_summary(enhanced),
        "mode_coverage": mode_coverage(enhanced_v12, enhanced_ict, enhanced_trades),
        "engine_attribution": source_attribution(enhanced_trades),
    }
    (folder / "comparison.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def write_report(results: dict[str, Any], validation: dict[str, Any], output: Path) -> None:
    lines = [
        "# V14.3 Satellite Symbol Profit Backtest",
        "",
        "EURUSD, AUDUSD and USDJPY use frozen high-conviction V12 and ICT sleeves. GBPUSD, GBPJPY, the existing V14.3 signal stream and the drawdown governor are unchanged.",
        "",
        "## ICT cross-window evidence",
        "",
        "| Symbol | Candidates | Development R/PF | Confirmation R/PF | Validation R/PF |",
        "|---|---:|---:|---:|---:|",
    ]
    for symbol in TARGET_SYMBOLS:
        item = validation[symbol]
        d, c, v = item["development"], item["confirmation"], item["validation"]
        lines.append(
            f"| {symbol} | {item['candidates']} | {d['net_r']:.2f}/{float(d['profit_factor'] or 0):.3f} | "
            f"{c['net_r']:.2f}/{float(c['profit_factor'] or 0):.3f} | "
            f"{v['net_r']:.2f}/{float(v['profit_factor'] or 0):.3f} |"
        )

    for name, result in results.items():
        baseline, enhanced, diff = result["baseline"], result["enhanced"], result["difference"]
        lines.extend([
            "",
            f"## {name}",
            "",
            "| Portfolio metric | Baseline | Enhanced satellites | Change |",
            "|---|---:|---:|---:|",
            f"| Net profit | ${baseline['net_profit']:,.2f} | ${enhanced['net_profit']:,.2f} | ${diff['net_profit']:,.2f} |",
            f"| Ending balance | ${baseline['ending_balance']:,.2f} | ${enhanced['ending_balance']:,.2f} | ${diff['ending_balance']:,.2f} |",
            f"| Profit factor | {baseline['profit_factor']:.4f} | {enhanced['profit_factor']:.4f} | {diff['profit_factor']:.4f} |",
            f"| Max closed DD | {baseline['max_closed_drawdown_percent']:.4f}% | {enhanced['max_closed_drawdown_percent']:.4f}% | {diff['max_drawdown_points']:.4f} pp |",
            f"| Stress DD | {baseline['stress_drawdown_percent']:.4f}% | {enhanced['stress_drawdown_percent']:.4f}% | {diff['stress_drawdown_points']:.4f} pp |",
            f"| Closed trades | {baseline['closed_trades']} | {enhanced['closed_trades']} | {diff['closed_trades']} |",
            "",
            "### Target-symbol profitability",
            "",
            "| Symbol | Baseline net | Enhanced net | Improvement | Enhanced PF |",
            "|---|---:|---:|---:|---:|",
        ])
        for symbol in TARGET_SYMBOLS:
            before = result["baseline_target_symbols"][symbol]
            after = result["enhanced_target_symbols"][symbol]
            lines.append(
                f"| {symbol} | ${before['net']:,.2f} | ${after['net']:,.2f} | "
                f"${after['net'] - before['net']:,.2f} | {after['profit_factor']:.3f} |"
            )
        lines.append(
            f"| **Combined** | ${result['baseline_target_symbols']['combined_net']:,.2f} | "
            f"${result['enhanced_target_symbols']['combined_net']:,.2f} | "
            f"${result['enhanced_target_symbols']['combined_net'] - result['baseline_target_symbols']['combined_net']:,.2f} | — |"
        )

    lines.extend([
        "",
        "## Status",
        "",
        "Research/shadow only. Broker-native spread, commission, swap, slippage and forward-test evidence are required before execution sizing is changed.",
    ])
    (output / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_results(results: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt

    ten = results["exact_10_year_window"]
    symbols = list(TARGET_SYMBOLS)
    baseline = [ten["baseline_target_symbols"][s]["net"] for s in symbols]
    enhanced = [ten["enhanced_target_symbols"][s]["net"] for s in symbols]
    positions = list(range(len(symbols)))
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.bar([p - 0.18 for p in positions], baseline, 0.36, label="Baseline")
    axis.bar([p + 0.18 for p in positions], enhanced, 0.36, label="Enhanced satellite")
    axis.set_xticks(positions, symbols)
    axis.set_ylabel("Exact ten-year net profit ($)")
    axis.set_title("Satellite Symbol Profit Improvement")
    axis.axhline(0, linewidth=1)
    axis.legend()
    for container in axis.containers:
        axis.bar_label(container, fmt="$%.0f", padding=3)
    figure.tight_layout()
    figure.savefig(output / "satellite_symbol_profit.png", dpi=160)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run high-conviction satellite symbol replay")
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    install_all_symbol_ict_profile()
    baseline_v12 = apply_weak_symbol_profile(load_v12(args.v12_ledger))
    existing_ict = load_ict_candidates(args.ict_source)
    new_ict, selection = build_new_ict_candidates(args.out)
    baseline_ict = combine_ict(existing_ict, new_ict)

    install_satellite_ict_risk()
    enhanced_v12 = apply_satellite_v12_risk(baseline_v12)
    enhanced_new_ict = filter_satellite_ict(new_ict)
    enhanced_new_ict.to_csv(args.out / "enhanced_new_ict_candidates.csv", index=False)
    enhanced_ict = combine_ict(existing_ict, enhanced_new_ict)
    validation = validation_windows(enhanced_new_ict)

    latest = max(baseline_v12["exit_time"].max(), baseline_ict["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    governor = DrawdownGovernor()
    results = {
        "full_repository_history": run_case(
            "full_repository_history",
            baseline_v12,
            baseline_ict,
            enhanced_v12,
            enhanced_ict,
            governor,
            args.out,
        ),
        "exact_10_year_window": run_case(
            "exact_10_year_window",
            filter_window(baseline_v12, start, latest),
            filter_window(baseline_ict, start, latest),
            filter_window(enhanced_v12, start, latest),
            filter_window(enhanced_ict, start, latest),
            governor,
            args.out,
        ),
    }
    payload = {
        "governor": governor.__dict__,
        "source_selection": selection,
        "satellite_validation": validation,
        "results": results,
    }
    (args.out / "all_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_report(results, validation, args.out)
    plot_results(results, args.out)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
