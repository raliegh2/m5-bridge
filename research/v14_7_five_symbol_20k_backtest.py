"""V14.7 five-symbol swing + ICT walk-forward research.

Acceptance objective:
* all five symbols contribute an independently validated swing sleeve;
* all five symbols contribute an independently validated ICT sleeve;
* $20,000 net profit from a $5,000 start after modeled retail costs;
* closed drawdown <= 9.60% and stressed drawdown <= 10.00%.

Candidate/ensemble selection uses training, validation and two audit blocks. The
final holdout block is not used to rank or choose a strategy. A sleeve is
admitted only after it also passes that untouched final block.
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
from mt5_ai_bridge.v14_7_strategy_families import (  # noqa: E402
    SYMBOLS,
    generate_symbol_candidates,
)
from research import v14_6_five_symbol_dual_engine_target as base  # noqa: E402
from research.v13_expanded_assets_backtest import load_frame  # noqa: E402
from research.v14_3_production_improved_backtest import filter_window  # noqa: E402

OUT = ROOT / "research" / "v14_7_five_symbol_20k_output"
FIXTURE = ROOT / "research" / "v14_6_candidate_fixture"
CURRENT_RESULTS = ROOT / "research" / "v14_6_2_raw_intraday_results.json"
STARTING_BALANCE = 5_000.0
TARGET_NET_PROFIT = 20_000.0
TARGET_ENDING_BALANCE = STARTING_BALANCE + TARGET_NET_PROFIT
MAX_CLOSED_DD = 9.60
MAX_STRESS_DD = 10.00
MAX_SWING_RISK = 1.25
MAX_ICT_RISK = 0.60
SWING_SCALE_GRID = tuple(round(0.60 + index * 0.10, 2) for index in range(13))  # .60..1.80
ICT_SCALE_GRID = tuple(round(0.50 + index * 0.10, 2) for index in range(21))  # .50..2.50


def load_fixture(name: str, mode: str, cost_r: float) -> pd.DataFrame:
    path = FIXTURE / name
    if not path.exists():
        raise FileNotFoundError(f"Missing V14.6 fixture: {path}")
    frame = pd.read_csv(path)
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["side"] = frame["side"].astype(str).str.upper()
    frame["mode"] = mode
    frame["family"] = f"V14_6_{mode}"
    frame["profile"] = frame.get("profile", frame["engine"]).astype(str)
    frame["selection_cost_r"] = float(cost_r)
    return frame.sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)


def build_raw_candidates() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in SYMBOLS:
        generated = generate_symbol_candidates(
            symbol,
            load_frame(symbol, "h1"),
            load_frame(symbol, "h4"),
            load_frame(symbol, "d1"),
        )
        if not generated.empty:
            frames.append(generated)
    if not frames:
        raise RuntimeError("No V14.7 raw-candle candidates were generated")
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    return output.sort_values(["entry_time", "symbol", "mode", "engine"]).reset_index(drop=True)


def ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "net_r": 0.0, "expectancy_r": None, "profit_factor": None}
    cost = frame["selection_cost_r"].astype(float)
    values = frame["r_multiple"].astype(float) - cost
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "expectancy_r": round(float(values.mean()), 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
    }


def blocks(start: pd.Timestamp, end: pd.Timestamp) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    span = end - start
    train_end = start + span * 0.35
    validation_end = start + span * 0.55
    audit_a_end = start + span * 0.70
    audit_b_end = start + span * 0.85
    return {
        "train": (start, train_end),
        "validation": (train_end, validation_end),
        "audit_a": (validation_end, audit_a_end),
        "audit_b": (audit_a_end, audit_b_end),
        "final_holdout": (audit_b_end, end + pd.Timedelta(seconds=1)),
    }


def slice_block(frame: pd.DataFrame, bounds: tuple[pd.Timestamp, pd.Timestamp]) -> pd.DataFrame:
    entry = pd.to_datetime(frame["entry_time"], utc=True)
    return frame[(entry >= bounds[0]) & (entry < bounds[1])].copy()


def stats_by_block(
    frame: pd.DataFrame, periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]]
) -> dict[str, dict[str, Any]]:
    return {name: ratio_stats(slice_block(frame, bounds)) for name, bounds in periods.items()}


def pass_learning(stats: dict[str, dict[str, Any]], mode: str) -> bool:
    minimum = {
        "SWING": {"train": 10, "validation": 5},
        "ICT": {"train": 16, "validation": 8},
    }[mode]
    for name in ("train", "validation"):
        item = stats[name]
        if item["trades"] < minimum[name] or item["net_r"] <= 0:
            return False
        if float(item["profit_factor"] or 0.0) < (1.08 if name == "train" else 1.04):
            return False
    return True


def pass_audits(stats: dict[str, dict[str, Any]], mode: str) -> bool:
    minimum = 3 if mode == "SWING" else 5
    for name in ("audit_a", "audit_b"):
        item = stats[name]
        if item["trades"] < minimum or item["net_r"] <= 0:
            return False
        if float(item["profit_factor"] or 0.0) <= 1.01:
            return False
    return True


def pass_final(stats: dict[str, dict[str, Any]], mode: str) -> bool:
    item = stats["final_holdout"]
    minimum = 3 if mode == "SWING" else 5
    return (
        item["trades"] >= minimum
        and item["net_r"] > 0
        and float(item["profit_factor"] or 0.0) > 1.02
    )


def learning_score(stats: dict[str, dict[str, Any]]) -> float:
    selected = [stats["train"], stats["validation"]]
    min_exp = min(float(item["expectancy_r"] or -99.0) for item in selected)
    min_pf = min(float(item["profit_factor"] or 0.0) for item in selected)
    return min_exp * 100.0 + min_pf * 5.0 + sum(float(item["net_r"]) for item in selected)


def audit_score(stats: dict[str, dict[str, Any]]) -> float:
    selected = [stats[name] for name in ("train", "validation", "audit_a", "audit_b")]
    min_exp = min(float(item["expectancy_r"] or -99.0) for item in selected)
    min_pf = min(float(item["profit_factor"] or 0.0) for item in selected)
    return min_exp * 100.0 + min_pf * 5.0 + sum(float(item["net_r"]) for item in selected)


def component_candidates(
    frame: pd.DataFrame,
    periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
    mode: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in base.filter_candidates(frame):
        selected = base.filter_frame(frame, spec)
        stats = stats_by_block(selected, periods)
        if not pass_learning(stats, mode):
            continue
        rows.append(
            {
                "spec": asdict(spec),
                "engine": spec.engine,
                "family": str(selected["family"].iloc[0]) if not selected.empty else "UNKNOWN",
                "stats": stats,
                "learning_score": round(learning_score(stats), 6),
                "frame": selected,
            }
        )
    rows.sort(key=lambda item: item["learning_score"], reverse=True)
    return rows[:12]


def combine_frames(frames: list[pd.DataFrame], mode: str) -> pd.DataFrame:
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    output["entry_hour"] = output["entry_time"].dt.floor("h")
    output = output.sort_values(["entry_time", "engine", "side"])
    output = output.drop_duplicates(["symbol", "mode", "entry_hour"], keep="first")
    output["entry_day"] = output["entry_time"].dt.floor("D")
    daily_limit = 2 if mode == "SWING" else 6
    output["daily_rank"] = output.groupby(["symbol", "mode", "entry_day"]).cumcount()
    output = output[output["daily_rank"] < daily_limit]
    return output.drop(columns=["entry_hour", "entry_day", "daily_rank"]).reset_index(drop=True)


def ensemble_candidates(
    components: list[dict[str, Any]],
    periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
    mode: str,
) -> list[dict[str, Any]]:
    if not components:
        return []
    pool = components[:8]
    rows: list[dict[str, Any]] = []
    for size in (1, 2, 3):
        for combo in itertools.combinations(pool, size):
            engines = [item["engine"] for item in combo]
            families = [item["family"] for item in combo]
            if len(set(engines)) != len(engines):
                continue
            combined = combine_frames([item["frame"] for item in combo], mode)
            stats = stats_by_block(combined, periods)
            if not pass_learning(stats, mode) or not pass_audits(stats, mode):
                continue
            rows.append(
                {
                    "components": [item["spec"] for item in combo],
                    "engines": engines,
                    "families": families,
                    "stats": stats,
                    "audit_score": round(audit_score(stats), 6),
                    "frame": combined,
                }
            )
    rows.sort(key=lambda item: item["audit_score"], reverse=True)
    return rows


def risk_from_stats(stats: dict[str, dict[str, Any]], mode: str) -> float:
    items = [stats[name] for name in stats]
    min_pf = min(float(item["profit_factor"] or 0.0) for item in items)
    min_exp = min(float(item["expectancy_r"] or 0.0) for item in items)
    if mode == "SWING":
        if min_pf >= 1.50 and min_exp >= 0.15:
            return 0.90
        if min_pf >= 1.30 and min_exp >= 0.08:
            return 0.70
        return 0.45
    if min_pf >= 1.50 and min_exp >= 0.12:
        return 0.40
    if min_pf >= 1.30 and min_exp >= 0.06:
        return 0.30
    return 0.20


def select_sleeve(
    symbol: str,
    mode: str,
    frame: pd.DataFrame,
    periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    components = component_candidates(frame, periods, mode)
    ensembles = ensemble_candidates(components, periods, mode)
    diagnostics: list[dict[str, Any]] = []
    for item in ensembles[:20]:
        diagnostics.append(
            {
                "components": item["components"],
                "engines": item["engines"],
                "families": item["families"],
                "audit_score": item["audit_score"],
                "stats": item["stats"],
                "final_passed": pass_final(item["stats"], mode),
            }
        )
    if not ensembles:
        return None, diagnostics
    winner = ensembles[0]  # frozen before inspecting the final holdout
    if not pass_final(winner["stats"], mode):
        return None, diagnostics
    risk = risk_from_stats(winner["stats"], mode)
    selected = winner["frame"].copy()
    setup = f"v14_7_{symbol.lower()}_{mode.lower()}"
    selected["setup"] = setup
    selected["risk_percent"] = risk
    selected["sleeve_mode"] = mode
    selected["raw_r_multiple"] = selected["r_multiple"].astype(float)
    selected["cost_r"] = selected["selection_cost_r"].astype(float)
    selected["r_multiple"] = selected["raw_r_multiple"] - selected["cost_r"]
    return (
        {
            "symbol": symbol,
            "mode": mode,
            "setup": setup,
            "base_risk_percent": risk,
            "components": winner["components"],
            "engines": winner["engines"],
            "families": winner["families"],
            "evidence": winner["stats"],
            "frame": selected,
        },
        diagnostics,
    )


def make_guard(symbol: str) -> SymbolGuard:
    sessions = {
        "GBPUSD": (6, 20),
        "EURUSD": (6, 20),
        "GBPJPY": (6, 20),
        "AUDUSD": (0, 19),
        "USDJPY": (0, 20),
    }
    start, end = sessions[symbol]
    return SymbolGuard(
        post_loss_multiplier=0.68,
        max_open_positions=2,
        max_entries_per_hour=1,
        daily_loss_cap_percent=1.00,
        stop_after_daily_losses=4,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=4.0,
        win_pressure_recovery=0.72,
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
    profit_profile.SYMBOL_GUARDS.update({symbol: make_guard(symbol) for symbol in SYMBOLS})
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
        swing_case = base.scale_risk(swing, swing_scale, MAX_SWING_RISK)
        ict_case = base.scale_risk(ict, ict_scale, MAX_ICT_RISK)
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
                float(summary["max_closed_drawdown_percent"]) <= MAX_CLOSED_DD
                and float(summary["stress_drawdown_percent"]) <= MAX_STRESS_DD
            ),
            "target_reached": float(summary["net_profit"]) >= TARGET_NET_PROFIT,
            "governor_interventions": int(len(events)),
        }
        rows.append(row)
        if row["safe"] and (best_safe is None or row["net_profit"] > best_safe["net_profit"]):
            best_safe = row
            best_outputs = (trades, skipped, events)
    if best_safe is None or best_outputs is None:
        raise RuntimeError("No V14.7 portfolio remained within the drawdown boundary")
    return best_safe, rows, *best_outputs


def activity(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "active_days": 0, "average_per_active_day": 0.0, "maximum_per_day": 0}
    entry = pd.to_datetime(frame["entry_time"], utc=True)
    counts = entry.dt.floor("D").value_counts()
    return {
        "trades": int(len(frame)),
        "active_days": int(len(counts)),
        "average_per_active_day": round(float(counts.mean()), 4),
        "maximum_per_day": int(counts.max()),
    }


def clean_selection(selection: dict[str, Any] | None) -> dict[str, Any] | None:
    if selection is None:
        return None
    return {key: value for key, value in selection.items() if key != "frame"}


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
    lines = [
        "# V14.7 Five-Symbol $20,000 Retail-Net Research",
        "",
        f"**Window:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        f"**Starting balance:** ${STARTING_BALANCE:,.2f}",
        f"**Target after retail costs and fees:** ${TARGET_NET_PROFIT:,.2f} net / ${TARGET_ENDING_BALANCE:,.2f} ending balance",
        "",
        "## Validated coverage",
        "",
        "| Symbol | Swing | ICT |",
        "|---|---:|---:|",
    ]
    for symbol in SYMBOLS:
        lines.append(
            f"| {symbol} | {payload['selections'][symbol]['SWING'] is not None} | {payload['selections'][symbol]['ICT'] is not None} |"
        )
    lines += [
        "",
        "## Best safe portfolio",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Net profit | ${best['net_profit']:,.2f} |",
        f"| Ending balance | ${best['ending_balance']:,.2f} |",
        f"| Profit factor | {float(best['profit_factor']):.4f} |",
        f"| Maximum closed drawdown | {best['max_closed_drawdown_percent']:.4f}% |",
        f"| Stressed drawdown | {best['stress_drawdown_percent']:.4f}% |",
        f"| Target reached | {best['target_reached']} |",
        f"| Gap to $20,000 | ${payload['target_gap']:,.2f} |",
        "",
        "## Method",
        "",
        "Strategies were chosen from training and validation evidence, challenged in two audit blocks, then accepted only if the untouched final holdout also remained profitable after costs. The final holdout was not used to rank candidates.",
        "",
        "Research only. Historical R-cost replay cannot guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw = build_raw_candidates()
    swing_fixture = load_fixture("all_swing_candidates.csv", "SWING", 0.04)
    ict_fixture = load_fixture("all_wide_ict_candidates.csv", "ICT", 0.09)
    pool = pd.concat([raw, swing_fixture, ict_fixture], ignore_index=True, sort=False)
    pool["entry_time"] = pd.to_datetime(pool["entry_time"], utc=True)
    pool["exit_time"] = pd.to_datetime(pool["exit_time"], utc=True)
    pool = pool.drop_duplicates(["entry_time", "exit_time", "symbol", "mode", "engine", "side"])
    pool.to_csv(OUT / "all_strategy_candidates.csv", index=False)

    latest = pool["exit_time"].max()
    start = latest - pd.DateOffset(years=10)
    pool = filter_window(pool, start, latest)
    periods = blocks(start, latest)

    selections: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    swing_frames: list[pd.DataFrame] = []
    ict_frames: list[pd.DataFrame] = []

    for symbol in SYMBOLS:
        selections[symbol] = {}
        diagnostics[symbol] = {}
        for mode in ("SWING", "ICT"):
            subset = pool[(pool["symbol"] == symbol) & (pool["mode"] == mode)].copy()
            selection, ranking = select_sleeve(symbol, mode, subset, periods)
            selections[symbol][mode] = clean_selection(selection)
            diagnostics[symbol][mode] = ranking
            if selection is not None:
                if mode == "SWING":
                    swing_frames.append(selection["frame"])
                else:
                    ict_frames.append(selection["frame"])

    if not swing_frames or not ict_frames:
        raise RuntimeError("V14.7 found no valid swing or ICT portfolio")
    swing = pd.concat(swing_frames, ignore_index=True, sort=False)
    ict = pd.concat(ict_frames, ignore_index=True, sort=False)
    swing.to_csv(OUT / "selected_swing_trades.csv", index=False)
    ict.to_csv(OUT / "selected_ict_trades.csv", index=False)

    best, search_rows, trades, skipped, events = portfolio_search(swing, ict)
    trades.to_csv(OUT / "best_safe_trades.csv", index=False)
    skipped.to_csv(OUT / "best_safe_skipped.csv", index=False)
    events.to_csv(OUT / "best_safe_governor_events.csv", index=False)
    write_csv(OUT / "portfolio_risk_search.csv", search_rows)

    current = json.loads(CURRENT_RESULTS.read_text()) if CURRENT_RESULTS.exists() else {}
    current_best = current.get("best_safe_portfolio", {})
    swing_symbols = [symbol for symbol in SYMBOLS if selections[symbol]["SWING"] is not None]
    ict_symbols = [symbol for symbol in SYMBOLS if selections[symbol]["ICT"] is not None]
    all_ten = len(swing_symbols) == len(SYMBOLS) and len(ict_symbols) == len(SYMBOLS)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "target": {
            "starting_balance": STARTING_BALANCE,
            "net_profit": TARGET_NET_PROFIT,
            "ending_balance": TARGET_ENDING_BALANCE,
            "after_retail_costs_and_fees": True,
        },
        "validation_protocol": {
            "training_percent": 35,
            "validation_percent": 20,
            "audit_a_percent": 15,
            "audit_b_percent": 15,
            "untouched_final_holdout_percent": 15,
            "final_holdout_used_for_selection": False,
        },
        "risk_limits": {
            "max_swing_trade_percent": MAX_SWING_RISK,
            "max_ict_trade_percent": MAX_ICT_RISK,
            "max_closed_drawdown_percent": MAX_CLOSED_DD,
            "max_stress_drawdown_percent": MAX_STRESS_DD,
            "max_ict_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_ict_open_risk_percent,
            "max_combined_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_combined_open_risk_percent,
        },
        "selections": selections,
        "diagnostics": diagnostics,
        "coverage": {
            "active_swing_symbols": swing_symbols,
            "active_ict_symbols": ict_symbols,
            "all_ten_sleeves_active": all_ten,
        },
        "activity": {
            "swing": activity(swing),
            "ict": activity(ict),
        },
        "previous_v14_6_2": current_best,
        "best_safe_portfolio": best,
        "profit_improvement_vs_v14_6_2": round(
            float(best["net_profit"]) - float(current_best.get("net_profit", 0.0)), 2
        ),
        "target_reached": bool(best["target_reached"]),
        "target_gap": round(max(0.0, TARGET_NET_PROFIT - float(best["net_profit"])), 2),
    }
    (OUT / "v14_7_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_report(payload)

    print(
        json.dumps(
            {
                "window": payload["window"],
                "coverage": payload["coverage"],
                "activity": payload["activity"],
                "previous_v14_6_2": current_best,
                "best_safe_portfolio": best,
                "profit_improvement_vs_v14_6_2": payload["profit_improvement_vs_v14_6_2"],
                "target_reached": payload["target_reached"],
                "target_gap": payload["target_gap"],
                "output": str(OUT),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
