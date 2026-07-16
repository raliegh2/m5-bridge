"""V14.6.1 multi-entry intraday ICT trend validation.

The experiment keeps the V14.6 swing research and existing validated wide ICT
sleeves, then adds multi-entry H1 trend-continuation candidates for the three
failed ICT symbols: GBPUSD, GBPJPY and AUDUSD.

Every candidate enters on the next completed-bar open.  Promotion still
requires positive after-cost development, confirmation and holdout evidence.
The script reports failure honestly when a symbol does not pass.
"""
from __future__ import annotations

import csv
import itertools
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile  # noqa: E402
from mt5_ai_bridge.v14_3_profit_preserving_profile import SymbolGuard  # noqa: E402
from mt5_ai_bridge.v14_6_1_intraday_ict_trend import generate_symbol_profiles  # noqa: E402
from research import v14_6_five_symbol_dual_engine_target as base  # noqa: E402
from research.v13_expanded_assets_backtest import load_frame  # noqa: E402
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates  # noqa: E402

OUT = ROOT / "research" / "v14_6_1_intraday_ict_output"
BASE_RESULTS = ROOT / "research" / "v14_6_five_symbol_results.json"
TARGET_SYMBOLS = ("GBPUSD", "GBPJPY", "AUDUSD")
INTRADAY_COST_R = 0.12
MAX_DAILY_INTRADAY_ENTRIES = 7
SWING_SCALE_GRID = base.SWING_SCALE_GRID
ICT_SCALE_GRID = tuple(round(0.50 + 0.10 * index, 2) for index in range(16))  # .50..2.00


def mixed_ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "net_r": 0.0, "expectancy_r": None, "profit_factor": None}
    costs = frame.get("selection_cost_r", pd.Series(0.0, index=frame.index)).astype(float)
    values = frame["r_multiple"].astype(float) - costs
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "expectancy_r": round(float(values.mean()), 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
    }


def select_mixed_cost_ict(
    symbol: str,
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
):
    candidates: list[dict[str, Any]] = []
    for spec in base.filter_candidates(frame):
        selected = base.filter_frame(frame, spec)
        segments = base.chronological_segments(selected, start, end)
        stats = {name: mixed_ratio_stats(segment) for name, segment in segments.items()}
        passed = base.sleeve_passes(stats, "ICT")
        candidates.append(
            {
                "spec": asdict(spec),
                "passed": passed,
                "score": round(base.evidence_score(stats), 6),
                "segments": stats,
                "total": mixed_ratio_stats(selected),
            }
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    passing = [item for item in candidates if item["passed"]]
    if not passing:
        return None, candidates[:75]
    winner = passing[0]
    spec = base.FilterSpec(**winner["spec"])
    risk = base.risk_from_evidence(winner["segments"], "ICT")
    setup_name = f"v14_6_1_{symbol.lower()}_ict_{abs(hash(str(spec))) % 10_000_000:07d}"
    selection = base.SleeveSelection(
        symbol=symbol,
        mode="ICT",
        filter_spec=spec,
        base_risk_percent=risk,
        setup_name=setup_name,
        evidence={
            "segments": winner["segments"],
            "total": winner["total"],
            "score": winner["score"],
        },
    )
    return selection, candidates[:75]


def build_intraday_candidates() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in TARGET_SYMBOLS:
        h1 = load_frame(symbol, "h1")
        h4 = load_frame(symbol, "h4")
        d1 = load_frame(symbol, "d1")
        generated = generate_symbol_profiles(symbol, h1, h4, d1)
        if not generated.empty:
            generated = generated.copy()
            generated["selection_cost_r"] = INTRADAY_COST_R
            generated["cost_class"] = "ICT_INTRADAY"
            frames.append(generated)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    return output.sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)


def daily_activity(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trading_days": 0,
            "average_entries_per_active_day": 0.0,
            "median_entries_per_active_day": 0.0,
            "p95_entries_per_active_day": 0.0,
            "maximum_entries_per_day": 0,
            "days_with_multiple_entries": 0,
        }
    entry = pd.to_datetime(frame["entry_time"], utc=True)
    counts = entry.dt.floor("D").value_counts()
    return {
        "trading_days": int(len(counts)),
        "average_entries_per_active_day": round(float(counts.mean()), 4),
        "median_entries_per_active_day": round(float(counts.median()), 4),
        "p95_entries_per_active_day": round(float(counts.quantile(0.95)), 4),
        "maximum_entries_per_day": int(counts.max()),
        "days_with_multiple_entries": int((counts > 1).sum()),
    }


def apply_mixed_selection(frame: pd.DataFrame, selection: base.SleeveSelection) -> pd.DataFrame:
    selected = base.filter_frame(frame, selection.filter_spec).copy()
    selected["setup"] = selection.setup_name
    selected["risk_percent"] = selection.base_risk_percent
    selected["sleeve_mode"] = "ICT"
    selected["raw_r_multiple"] = selected["r_multiple"].astype(float)
    selected["cost_r"] = selected["selection_cost_r"].astype(float)
    selected["r_multiple"] = selected["raw_r_multiple"] - selected["cost_r"]
    return selected


def make_intraday_guard(symbol: str) -> SymbolGuard:
    sessions = {
        "GBPUSD": (6, 20),
        "EURUSD": (6, 19),
        "GBPJPY": (6, 20),
        "AUDUSD": (0, 18),
        "USDJPY": (0, 20),
    }
    start, end = sessions[symbol]
    multi_entry = symbol in TARGET_SYMBOLS
    return SymbolGuard(
        post_loss_multiplier=0.65 if multi_entry else 0.70,
        max_open_positions=2 if multi_entry else 1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=0.90,
        stop_after_daily_losses=4 if multi_entry else 3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=4.0 if multi_entry else 6.0,
        win_pressure_recovery=0.70 if multi_entry else 0.75,
        session_start_hour_utc=start,
        session_end_hour_utc=end,
    )


def install_profile(ict: pd.DataFrame) -> tuple[dict, dict]:
    old_risk = dict(profit_profile.SETUP_RISK_PERCENT)
    old_guards = dict(profit_profile.SYMBOL_GUARDS)
    profit_profile.SETUP_RISK_PERCENT.clear()
    for row in ict[["symbol", "setup", "risk_percent"]].drop_duplicates().itertuples(index=False):
        profit_profile.SETUP_RISK_PERCENT[(str(row.symbol).upper(), str(row.setup))] = float(row.risk_percent)
    profit_profile.SYMBOL_GUARDS.clear()
    profit_profile.SYMBOL_GUARDS.update({symbol: make_intraday_guard(symbol) for symbol in base.SYMBOLS})
    return old_risk, old_guards


def restore_profile(old_risk: dict, old_guards: dict) -> None:
    profit_profile.SETUP_RISK_PERCENT.clear()
    profit_profile.SETUP_RISK_PERCENT.update(old_risk)
    profit_profile.SYMBOL_GUARDS.clear()
    profit_profile.SYMBOL_GUARDS.update(old_guards)


def portfolio_search(swing: pd.DataFrame, ict: pd.DataFrame):
    rows: list[dict[str, Any]] = []
    best_safe: dict[str, Any] | None = None
    best_outputs = None
    for swing_scale, ict_scale in itertools.product(SWING_SCALE_GRID, ICT_SCALE_GRID):
        swing_case = base.scale_risk(swing, swing_scale, base.MAX_SWING_RISK)
        ict_case = base.scale_risk(ict, ict_scale, base.MAX_ICT_RISK)
        old_risk, old_guards = install_profile(ict_case)
        try:
            summary, trades, skipped, events = base.run_portfolio(swing_case, ict_case)
        finally:
            restore_profile(old_risk, old_guards)
        row = {
            "swing_scale": swing_scale,
            "ict_scale": ict_scale,
            "net_profit": summary["net_profit"],
            "ending_balance": summary["ending_balance"],
            "return_percent": summary["return_percent"],
            "profit_factor": summary["profit_factor"],
            "max_closed_drawdown_percent": summary["max_closed_drawdown_percent"],
            "stress_drawdown_percent": summary["stress_drawdown_percent"],
            "closed_trades": summary["closed_trades"],
            "skipped_trades": summary["skipped_ict_trades"],
            "safe": (
                float(summary["max_closed_drawdown_percent"]) <= base.MAX_CLOSED_DD
                and float(summary["stress_drawdown_percent"]) <= base.MAX_STRESS_DD
            ),
            "target_reached": float(summary["net_profit"]) >= base.TARGET_NET_PROFIT,
            "governor_interventions": int(len(events)),
        }
        rows.append(row)
        if row["safe"] and (best_safe is None or row["net_profit"] > best_safe["net_profit"]):
            best_safe = row
            best_outputs = (trades, skipped, events)
    if best_safe is None or best_outputs is None:
        raise RuntimeError("No V14.6.1 portfolio stayed within the drawdown boundary")
    return best_safe, rows, *best_outputs


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(payload: dict[str, Any]) -> None:
    best = payload["best_safe_portfolio"]
    baseline = payload["baseline_v14_6"]
    lines = [
        "# V14.6.1 Multi-Entry Intraday ICT Trend Research",
        "",
        f"**Window:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        "**Starting balance:** $5,000.00",
        "**Retail-net target:** $34,000.00",
        "",
        "## ICT validation",
        "",
        "| Symbol | V14.6 ICT | V14.6.1 ICT | Selected engine | Active-day average | Max entries/day |",
        "|---|---|---|---|---:|---:|",
    ]
    for symbol in TARGET_SYMBOLS:
        item = payload["target_symbol_results"][symbol]
        lines.append(
            f"| {symbol} | Failed | {item['validated']} | {item.get('selected_engine') or '-'} | "
            f"{item['activity']['average_entries_per_active_day']:.2f} | {item['activity']['maximum_entries_per_day']} |"
        )
    lines += [
        "",
        "## Safe portfolio comparison",
        "",
        "| Metric | V14.6 | V14.6.1 |",
        "|---|---:|---:|",
        f"| Net profit | ${baseline['net_profit']:,.2f} | ${best['net_profit']:,.2f} |",
        f"| Ending balance | ${baseline['ending_balance']:,.2f} | ${best['ending_balance']:,.2f} |",
        f"| Profit factor | {baseline['profit_factor']:.4f} | {best['profit_factor']:.4f} |",
        f"| Closed drawdown | {baseline['max_closed_drawdown_percent']:.4f}% | {best['max_closed_drawdown_percent']:.4f}% |",
        f"| Stress drawdown | {baseline['stress_drawdown_percent']:.4f}% | {best['stress_drawdown_percent']:.4f}% |",
        f"| Target reached | False | {payload['target_reached']} |",
        "",
        "## Controls",
        "",
        "- Entries use completed H1 signals and the next H1 open; forming candles are never used.",
        "- Target symbols may hold at most two ICT positions and admit at most one new trade per hour.",
        "- Each profile caps candidate generation at five to seven entries per day.",
        "- Partial profit and break-even logic is simulated conservatively with stop-first ordering inside ambiguous candles.",
        "- The 1.75% ICT cap, 3.25% combined cap and 7.5/8.5/9.0/9.6 drawdown governor remain active.",
        "",
        "Research only. Historical R-cost results do not guarantee broker-native profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    swing_all = base.build_continuous_swing_candidates()
    wide_ict_all = base.build_continuous_ict_candidates()
    intraday_all = build_intraday_candidates()
    intraday_all.to_csv(OUT / "all_intraday_ict_candidates.csv", index=False)

    latest = max(swing_all["exit_time"].max(), wide_ict_all["exit_time"].max(), intraday_all["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    swing_window = filter_window(swing_all, start, latest)
    wide_window = filter_window(wide_ict_all, start, latest).copy()
    intraday_window = filter_window(intraday_all, start, latest).copy()
    wide_window["selection_cost_r"] = base.ALL_IN_COST_R["ICT_WIDE"]
    wide_window["cost_class"] = "ICT_WIDE"
    combined_ict = pd.concat([wide_window, intraday_window], ignore_index=True, sort=False)

    selections: dict[str, dict[str, Any]] = {}
    rankings: dict[str, Any] = {}
    selected_swing: list[pd.DataFrame] = []
    selected_ict: list[pd.DataFrame] = []
    target_results: dict[str, Any] = {}

    for symbol in base.SYMBOLS:
        selections[symbol] = {}
        rankings[symbol] = {}
        swing_symbol = swing_window[swing_window["symbol"] == symbol].copy()
        ict_symbol = combined_ict[combined_ict["symbol"] == symbol].copy()
        swing_selection, swing_ranking = base.select_sleeve(
            symbol, "SWING", swing_symbol, base.ALL_IN_COST_R["SWING"], start, latest
        )
        ict_selection, ict_ranking = select_mixed_cost_ict(symbol, ict_symbol, start, latest)
        selections[symbol]["SWING"] = None if swing_selection is None else asdict(swing_selection)
        selections[symbol]["ICT"] = None if ict_selection is None else asdict(ict_selection)
        rankings[symbol]["SWING"] = swing_ranking
        rankings[symbol]["ICT"] = ict_ranking
        if swing_selection is not None:
            selected_swing.append(base.apply_selection(swing_symbol, swing_selection))
        if ict_selection is not None:
            selected = apply_mixed_selection(ict_symbol, ict_selection)
            selected_ict.append(selected)
        if symbol in TARGET_SYMBOLS:
            chosen = pd.DataFrame() if ict_selection is None else base.filter_frame(ict_symbol, ict_selection.filter_spec)
            target_results[symbol] = {
                "validated": ict_selection is not None,
                "selected_engine": None if ict_selection is None else ict_selection.filter_spec.engine,
                "base_risk_percent": None if ict_selection is None else ict_selection.base_risk_percent,
                "evidence": None if ict_selection is None else ict_selection.evidence,
                "activity": daily_activity(chosen),
                "candidate_count": int(len(ict_symbol)),
                "intraday_candidate_count": int(len(intraday_window[intraday_window["symbol"] == symbol])),
            }

    if not selected_swing or not selected_ict:
        raise RuntimeError("No validated V14.6.1 portfolio sleeves")
    swing_selected = pd.concat(selected_swing, ignore_index=True, sort=False)
    swing_selected = base.all_in_cost(swing_selected, base.ALL_IN_COST_R["SWING"])
    ict_selected = pd.concat(selected_ict, ignore_index=True, sort=False)

    try:
        legacy = load_ict_candidates(base.LEGACY_ICT_SOURCE)
        legacy = filter_window(legacy, start, latest)
    except Exception:
        legacy = pd.DataFrame()
    if not legacy.empty:
        legacy = legacy.copy()
        legacy["setup"] = legacy.apply(
            lambda row: f"v14_6_1_legacy_{str(row['symbol']).lower()}_{str(row['setup']).lower()}",
            axis=1,
        )
        legacy["risk_percent"] = base.OBSERVATION_RISK
        legacy = base.all_in_cost(legacy, base.ALL_IN_COST_R["ICT_LEGACY_M1"])
        ict_selected = pd.concat([ict_selected, legacy], ignore_index=True, sort=False)

    best_safe, search_rows, best_trades, best_skipped, best_events = portfolio_search(
        swing_selected, ict_selected
    )
    best_trades.to_csv(OUT / "best_safe_trades.csv", index=False)
    best_skipped.to_csv(OUT / "best_safe_skipped.csv", index=False)
    best_events.to_csv(OUT / "best_safe_governor_events.csv", index=False)
    write_csv(OUT / "portfolio_risk_search.csv", search_rows)

    baseline_payload = json.loads(BASE_RESULTS.read_text(encoding="utf-8"))
    baseline = baseline_payload["best_safe_portfolio"]
    active_swing = sorted(symbol for symbol in base.SYMBOLS if selections[symbol]["SWING"] is not None)
    active_ict = sorted(symbol for symbol in base.SYMBOLS if selections[symbol]["ICT"] is not None)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "retail_cost_r": {
            "SWING": base.ALL_IN_COST_R["SWING"],
            "ICT_WIDE": base.ALL_IN_COST_R["ICT_WIDE"],
            "ICT_INTRADAY": INTRADAY_COST_R,
            "ICT_LEGACY_M1": base.ALL_IN_COST_R["ICT_LEGACY_M1"],
        },
        "execution_limits": {
            "target_symbol_max_open_ict_positions": 2,
            "max_entries_per_hour": 1,
            "candidate_daily_limit": MAX_DAILY_INTRADAY_ENTRIES,
            "max_ict_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_ict_open_risk_percent,
            "max_combined_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_combined_open_risk_percent,
        },
        "selections": selections,
        "candidate_rankings": rankings,
        "target_symbol_results": target_results,
        "coverage": {
            "active_swing_symbols": active_swing,
            "active_ict_symbols": active_ict,
            "all_three_failed_ict_symbols_fixed": all(target_results[s]["validated"] for s in TARGET_SYMBOLS),
            "all_ten_sleeves_active": set(active_swing) == set(base.SYMBOLS) and set(active_ict) == set(base.SYMBOLS),
        },
        "baseline_v14_6": baseline,
        "best_safe_portfolio": best_safe,
        "profit_improvement_vs_v14_6": round(float(best_safe["net_profit"]) - float(baseline["net_profit"]), 2),
        "target_reached": float(best_safe["net_profit"]) >= base.TARGET_NET_PROFIT,
        "target_gap": round(max(0.0, base.TARGET_NET_PROFIT - float(best_safe["net_profit"])), 2),
        "attribution": base.attribution(best_trades),
    }
    (OUT / "v14_6_1_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_report(payload)
    print(json.dumps({
        "window": payload["window"],
        "target_symbol_results": target_results,
        "coverage": payload["coverage"],
        "baseline_v14_6": baseline,
        "best_safe_portfolio": best_safe,
        "profit_improvement_vs_v14_6": payload["profit_improvement_vs_v14_6"],
        "target_reached": payload["target_reached"],
        "target_gap": payload["target_gap"],
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
