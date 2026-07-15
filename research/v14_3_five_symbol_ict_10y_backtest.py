"""Ten-year research replay for V12 plus V14.3 ICT on all five symbols.

Existing GBPUSD/GBPJPY ICT candidates remain unchanged. EURUSD, AUDUSD and
USDJPY candidates are regenerated from public completed-candle H1/H4/D1 data,
selected on the first 65% of each symbol's history, reported on the later 35%,
and admitted to the portfolio only when the frozen validation gate passes.
This file never connects to MT5 or places an order.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile
from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_all_symbol_ict import (
    ENGINE_BY_SYMBOL,
    SETUP_BY_SYMBOL,
    performance,
    prepare_frames,
    select_profile,
)
from mt5_ai_bridge.v14_3_profit_preserving_profile import SymbolGuard
from research.v13_expanded_assets_backtest import load_frame
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates, load_v12
from research.v14_3_profit_preserving_backtest import ResearchReplay

NEW_ICT_SYMBOLS = ("EURUSD", "AUDUSD", "USDJPY")
ALL_SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
VALIDATION_GATE = {
    "minimum_trades": 18,
    "minimum_net_r": 0.0,
    "minimum_profit_factor": 1.03,
}
NEW_ICT_BASE_RISK = {
    ("EURUSD", SETUP_BY_SYMBOL["EURUSD"]): 0.14,
    ("AUDUSD", SETUP_BY_SYMBOL["AUDUSD"]): 0.12,
    ("USDJPY", SETUP_BY_SYMBOL["USDJPY"]): 0.10,
}
NEW_ICT_GUARDS = {
    "EURUSD": SymbolGuard(
        post_loss_multiplier=0.75,
        max_open_positions=1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=0.70,
        stop_after_daily_losses=3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=4.0,
        win_pressure_recovery=0.75,
        session_start_hour_utc=7,
        session_end_hour_utc=18,
    ),
    "AUDUSD": SymbolGuard(
        post_loss_multiplier=0.75,
        max_open_positions=1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=0.60,
        stop_after_daily_losses=3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=4.0,
        win_pressure_recovery=0.75,
        session_start_hour_utc=6,
        session_end_hour_utc=18,
    ),
    "USDJPY": SymbolGuard(
        post_loss_multiplier=0.70,
        max_open_positions=1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=0.50,
        stop_after_daily_losses=3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=4.0,
        win_pressure_recovery=0.75,
        session_start_hour_utc=7,
        session_end_hour_utc=18,
    ),
}


def install_all_symbol_ict_profile() -> None:
    """Extend the existing mutable research profile for this process only."""
    profit_profile.SETUP_RISK_PERCENT.update(NEW_ICT_BASE_RISK)
    profit_profile.SYMBOL_GUARDS.update(NEW_ICT_GUARDS)


def validation_passed(report: dict[str, Any]) -> bool:
    validation = report["profiles"][report["selected"]]["validation"]
    profit_factor = float(validation["profit_factor"] or 0.0)
    return (
        int(validation["trades"]) >= VALIDATION_GATE["minimum_trades"]
        and float(validation["net_r"]) > VALIDATION_GATE["minimum_net_r"]
        and profit_factor >= VALIDATION_GATE["minimum_profit_factor"]
    )


def build_new_ict_candidates(output: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames: list[pd.DataFrame] = []
    reports: dict[str, Any] = {}
    candidate_dir = output / "new_ict_candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)

    for symbol in NEW_ICT_SYMBOLS:
        raw_h1 = load_frame(symbol, "h1")
        raw_h4 = load_frame(symbol, "h4")
        raw_d1 = load_frame(symbol, "d1")
        h1, _, _ = prepare_frames(raw_h1, raw_h4, raw_d1)
        start = h1["time"].min()
        end = h1["time"].max()
        split = start + (end - start) * 0.65
        selected, candidates, report = select_profile(symbol, h1, split)
        passed = validation_passed(report)
        report.update(
            {
                "symbol": symbol,
                "engine": ENGINE_BY_SYMBOL[symbol],
                "setup": SETUP_BY_SYMBOL[symbol],
                "data_start": start.isoformat(),
                "data_end": end.isoformat(),
                "development_end": split.isoformat(),
                "validation_gate": VALIDATION_GATE,
                "validation_passed": passed,
                "selected_profile": asdict(selected),
                "candidate_count": int(len(candidates)),
            }
        )
        candidates.to_csv(candidate_dir / f"{symbol.lower()}_shadow_candidates.csv", index=False)
        (candidate_dir / f"{symbol.lower()}_selection.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        reports[symbol] = report
        if passed and not candidates.empty:
            frames.append(candidates)

    if not frames:
        columns = ["symbol", "engine", "setup", "profile", "side", "entry_time", "exit_time", "r_multiple"]
        return pd.DataFrame(columns=columns), reports
    combined = pd.concat(frames, ignore_index=True).sort_values(["entry_time", "symbol", "engine"])
    combined = combined.drop_duplicates(["entry_time", "exit_time", "symbol", "engine", "side"])
    combined.to_csv(output / "admitted_new_ict_candidates.csv", index=False)
    return combined.reset_index(drop=True), reports


def _profit_factor(values: pd.Series) -> float | None:
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return gross_profit / gross_loss if gross_loss else (None if gross_profit == 0 else float("inf"))


def source_attribution(trades: pd.DataFrame) -> dict[str, dict[str, float | int | None]]:
    result: dict[str, dict[str, float | int | None]] = {}
    if trades.empty:
        return result
    for engine, group in trades.groupby("engine"):
        pnl = group["pnl"].astype(float)
        result[str(engine)] = {
            "trades": int(len(group)),
            "net_profit": float(pnl.sum()),
            "profit_factor": _profit_factor(pnl),
        }
    return result


def mode_coverage(v12: pd.DataFrame, ict: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for symbol in ALL_SYMBOLS:
        v12_rows = v12[v12["symbol"] == symbol]
        ict_rows = ict[ict["symbol"] == symbol]
        accepted = trades[trades["symbol"] == symbol] if not trades.empty else pd.DataFrame()
        result[symbol] = {
            "v12_enabled": bool(len(v12_rows)),
            "ict_enabled": bool(len(ict_rows)),
            "v12_engines": sorted(v12_rows["engine"].astype(str).unique().tolist()),
            "ict_engines": sorted(ict_rows["engine"].astype(str).unique().tolist()),
            "v12_candidates": int(len(v12_rows)),
            "ict_candidates": int(len(ict_rows)),
            "accepted_trades": int(len(accepted)),
            "accepted_v12": int((accepted["engine_group"] == "V12").sum()) if not accepted.empty else 0,
            "accepted_ict": int((accepted["engine_group"] == "ICT").sum()) if not accepted.empty else 0,
        }
    return result


def run_case(
    name: str,
    v12: pd.DataFrame,
    existing_ict: pd.DataFrame,
    new_ict: pd.DataFrame,
    output: Path,
) -> dict[str, Any]:
    current_summary, current_trades, current_skipped = ResearchReplay(v12, existing_ict).run()
    all_ict = pd.concat([existing_ict, new_ict], ignore_index=True, sort=False)
    all_ict = all_ict.sort_values(["entry_time", "symbol", "engine"]).drop_duplicates(
        ["entry_time", "exit_time", "symbol", "engine", "side"]
    )
    enhanced_summary, enhanced_trades, enhanced_skipped = ResearchReplay(v12, all_ict).run()

    folder = output / name
    folder.mkdir(parents=True, exist_ok=True)
    current_trades.to_csv(folder / "current_trades.csv", index=False)
    current_skipped.to_csv(folder / "current_skipped.csv", index=False)
    enhanced_trades.to_csv(folder / "five_symbol_ict_trades.csv", index=False)
    enhanced_skipped.to_csv(folder / "five_symbol_ict_skipped.csv", index=False)
    all_ict.to_csv(folder / "all_ict_candidates.csv", index=False)

    result = {
        "case": name,
        "current": current_summary,
        "five_symbol_ict": enhanced_summary,
        "difference": {
            "net_profit": enhanced_summary["net_profit"] - current_summary["net_profit"],
            "ending_balance": enhanced_summary["ending_balance"] - current_summary["ending_balance"],
            "profit_factor": enhanced_summary["profit_factor"] - current_summary["profit_factor"],
            "max_drawdown_points": enhanced_summary["max_closed_drawdown_percent"] - current_summary["max_closed_drawdown_percent"],
            "stress_drawdown_points": enhanced_summary["stress_drawdown_percent"] - current_summary["stress_drawdown_percent"],
            "trades": enhanced_summary["closed_trades"] - current_summary["closed_trades"],
        },
        "mode_coverage": mode_coverage(v12, all_ict, enhanced_trades),
        "engine_attribution": source_attribution(enhanced_trades),
    }
    (folder / "comparison.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def write_report(results: dict[str, Any], selection: dict[str, Any], output: Path) -> None:
    lines = [
        "# V12 + V14.3 Five-Symbol ICT Backtest",
        "",
        "EURUSD, AUDUSD and USDJPY ICT engines are shadow/research engines generated from completed public H1/H4/D1 candles. Existing GBPUSD and GBPJPY ICT streams are unchanged.",
        "",
        "## New ICT validation",
        "",
        "| Symbol | Engine | Selected profile | Candidates | Validation passed | Validation trades | Validation net R | Validation PF |",
        "|---|---|---|---:|---|---:|---:|---:|",
    ]
    for symbol in NEW_ICT_SYMBOLS:
        item = selection[symbol]
        validation = item["profiles"][item["selected"]]["validation"]
        lines.append(
            f"| {symbol} | {item['engine']} | {item['selected']} | {item['candidate_count']} | "
            f"{item['validation_passed']} | {validation['trades']} | {validation['net_r']:.2f} | "
            f"{float(validation['profit_factor'] or 0.0):.3f} |"
        )

    for name, result in results.items():
        current = result["current"]
        enhanced = result["five_symbol_ict"]
        diff = result["difference"]
        lines.extend(
            [
                "",
                f"## {name}",
                "",
                "| Metric | Current V12 + GBP ICT | Five-symbol ICT | Change |",
                "|---|---:|---:|---:|",
                f"| Net profit | ${current['net_profit']:,.2f} | ${enhanced['net_profit']:,.2f} | ${diff['net_profit']:,.2f} |",
                f"| Ending balance | ${current['ending_balance']:,.2f} | ${enhanced['ending_balance']:,.2f} | ${diff['ending_balance']:,.2f} |",
                f"| Profit factor | {current['profit_factor']:.3f} | {enhanced['profit_factor']:.3f} | {diff['profit_factor']:.3f} |",
                f"| Max closed DD | {current['max_closed_drawdown_percent']:.3f}% | {enhanced['max_closed_drawdown_percent']:.3f}% | {diff['max_drawdown_points']:.3f} pp |",
                f"| Stress DD | {current['stress_drawdown_percent']:.3f}% | {enhanced['stress_drawdown_percent']:.3f}% | {diff['stress_drawdown_points']:.3f} pp |",
                f"| Closed trades | {current['closed_trades']} | {enhanced['closed_trades']} | {diff['trades']} |",
                "",
                "### Profit by symbol",
                "",
                "| Symbol | Trades | Net profit | PF | V12 candidates | ICT candidates |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for symbol in ALL_SYMBOLS:
            stats = enhanced["by_symbol"].get(symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0})
            coverage = result["mode_coverage"][symbol]
            lines.append(
                f"| {symbol} | {stats['trades']} | ${stats['net']:,.2f} | {stats['profit_factor']:.3f} | "
                f"{coverage['v12_candidates']} | {coverage['ict_candidates']} |"
            )
    lines.extend(
        [
            "",
            "## Status",
            "",
            "Research/shadow only. Passing a historical gate does not authorize funded or unattended execution. Broker-native spread, commission, swap, slippage and forward-test evidence are still required.",
        ]
    )
    (output / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_results(results: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt

    labels = list(results)
    current = [results[name]["current"]["net_profit"] for name in labels]
    enhanced = [results[name]["five_symbol_ict"]["net_profit"] for name in labels]
    positions = list(range(len(labels)))
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.bar([position - 0.18 for position in positions], current, 0.36, label="Current")
    axis.bar([position + 0.18 for position in positions], enhanced, 0.36, label="Five-symbol ICT")
    axis.set_xticks(positions, labels)
    axis.set_ylabel("Net profit ($)")
    axis.set_title("Five-Symbol ICT Portfolio Comparison")
    axis.legend()
    for container in axis.containers:
        axis.bar_label(container, fmt="$%.0f", padding=3)
    figure.tight_layout()
    figure.savefig(output / "profit_comparison.png", dpi=160)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run five-symbol ICT shadow and portfolio replay")
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    install_all_symbol_ict_profile()
    v12 = apply_weak_symbol_profile(load_v12(args.v12_ledger))
    existing_ict = load_ict_candidates(args.ict_source)
    new_ict, selection = build_new_ict_candidates(args.out)

    latest = max(v12["exit_time"].max(), existing_ict["exit_time"].max(), new_ict["exit_time"].max())
    ten_year_start = latest - pd.DateOffset(years=10)
    results = {
        "full_repository_history": run_case("full_repository_history", v12, existing_ict, new_ict, args.out),
        "exact_10_year_window": run_case(
            "exact_10_year_window",
            filter_window(v12, ten_year_start, latest),
            filter_window(existing_ict, ten_year_start, latest),
            filter_window(new_ict, ten_year_start, latest),
            args.out,
        ),
    }
    payload = {"selection": selection, "results": results}
    (args.out / "all_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_report(results, selection, args.out)
    plot_results(results, args.out)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
