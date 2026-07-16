"""V14.6.1 profile-specific multi-entry ICT ensemble research.

This replay uses the complete V14.6 candidate bundle produced by the prior
GitHub Actions run.  It does not invent candles or duplicate trades.  Instead,
it fixes a weakness in V14.6 selection: all ICT profiles for a symbol shared
one engine name and were evaluated as one mixed stream.

V14.6.1 evaluates profile/side/hour components separately, selects components
using development and confirmation only, combines up to three low-overlap
components per failed symbol, and then applies an untouched holdout gate.  The
selected ensemble can take several trend-biased H1 entries per day because the
underlying profiles use different London/New York/Asia-London windows.
"""
from __future__ import annotations

import csv
import itertools
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile  # noqa: E402
from mt5_ai_bridge.v14_3_profit_preserving_profile import SymbolGuard  # noqa: E402
from research import v14_6_five_symbol_dual_engine_target as base  # noqa: E402
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates  # noqa: E402

OUT = ROOT / "research" / "v14_6_1_intraday_ict_output"
FIXTURE = ROOT / "research" / "v14_6_candidate_fixture"
SWING_FIXTURE = FIXTURE / "all_swing_candidates.csv"
ICT_FIXTURE = FIXTURE / "all_wide_ict_candidates.csv"
BASE_RESULTS = ROOT / "research" / "v14_6_five_symbol_results.json"
TARGET_SYMBOLS = ("GBPUSD", "GBPJPY", "AUDUSD")
INTRADAY_COST_R = 0.12
WIDE_COST_R = base.ALL_IN_COST_R["ICT_WIDE"]
MAX_COMPONENTS = 3
SWING_SCALE_GRID = base.SWING_SCALE_GRID
ICT_SCALE_GRID = tuple(round(0.50 + 0.10 * index, 2) for index in range(16))


@dataclass(frozen=True)
class ComponentSpec:
    profile: str
    side: str = "ALL"
    hour: int | None = None
    excluded_weekday: int | None = None


def load_candidates(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing V14.6 candidate fixture: {path}. The workflow must download "
            "artifact 8391937431 before running this script."
        )
    frame = pd.read_csv(path)
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["side"] = frame["side"].astype(str).str.upper()
    return frame.sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)


def net_stats(frame: pd.DataFrame, cost_r: float) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "net_r": 0.0, "expectancy_r": None, "profit_factor": None}
    values = frame["r_multiple"].astype(float) - float(cost_r)
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "expectancy_r": round(float(values.mean()), 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
    }


def segments(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    span = end - start
    dev_end = start + span * 0.50
    confirmation_end = start + span * 0.75
    entry = pd.to_datetime(frame["entry_time"], utc=True)
    return {
        "development": frame[(entry >= start) & (entry < dev_end)].copy(),
        "confirmation": frame[(entry >= dev_end) & (entry < confirmation_end)].copy(),
        "holdout": frame[(entry >= confirmation_end) & (entry <= end)].copy(),
    }


def filter_component(frame: pd.DataFrame, spec: ComponentSpec) -> pd.DataFrame:
    output = frame[frame["profile"].astype(str) == spec.profile].copy()
    if spec.side != "ALL":
        output = output[output["side"].astype(str) == spec.side]
    entry = pd.to_datetime(output["entry_time"], utc=True)
    if spec.hour is not None:
        output = output[entry.dt.hour == spec.hour]
        entry = pd.to_datetime(output["entry_time"], utc=True)
    if spec.excluded_weekday is not None:
        output = output[entry.dt.weekday != spec.excluded_weekday]
    return output


def component_specs(frame: pd.DataFrame) -> list[ComponentSpec]:
    specs: set[ComponentSpec] = set()
    for profile, group in frame.groupby("profile"):
        profile = str(profile)
        specs.add(ComponentSpec(profile))
        sides = sorted(group["side"].astype(str).unique().tolist())
        for side in sides:
            specs.add(ComponentSpec(profile, side=side))
        for weekday in range(5):
            specs.add(ComponentSpec(profile, excluded_weekday=weekday))
            for side in sides:
                specs.add(ComponentSpec(profile, side=side, excluded_weekday=weekday))
        hours = pd.to_datetime(group["entry_time"], utc=True).dt.hour
        for hour, count in hours.value_counts().items():
            if int(count) >= 15:
                specs.add(ComponentSpec(profile, hour=int(hour)))
                for side in sides:
                    specs.add(ComponentSpec(profile, side=side, hour=int(hour)))
    return sorted(
        specs,
        key=lambda item: (
            item.profile,
            item.side,
            -1 if item.hour is None else item.hour,
            -1 if item.excluded_weekday is None else item.excluded_weekday,
        ),
    )


def development_pass(stats: dict[str, dict[str, Any]]) -> bool:
    development = stats["development"]
    confirmation = stats["confirmation"]
    return (
        development["trades"] >= 10
        and confirmation["trades"] >= 5
        and development["net_r"] > 0
        and confirmation["net_r"] > 0
        and float(development["profit_factor"] or 0.0) >= 1.08
        and float(confirmation["profit_factor"] or 0.0) >= 1.03
    )


def component_score(stats: dict[str, dict[str, Any]]) -> float:
    development = stats["development"]
    confirmation = stats["confirmation"]
    min_expectancy = min(
        float(development["expectancy_r"] or -99.0),
        float(confirmation["expectancy_r"] or -99.0),
    )
    min_pf = min(
        float(development["profit_factor"] or 0.0),
        float(confirmation["profit_factor"] or 0.0),
    )
    return min_expectancy * 100.0 + min_pf * 5.0 + development["net_r"] + confirmation["net_r"]


def trade_keys(frame: pd.DataFrame) -> set[tuple]:
    return set(
        zip(
            pd.to_datetime(frame["entry_time"], utc=True).astype(str),
            frame["symbol"].astype(str),
            frame["side"].astype(str),
            frame["profile"].astype(str),
        )
    )


def select_component_ensemble(
    symbol: str,
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_r: float,
) -> tuple[base.SleeveSelection | None, pd.DataFrame, list[dict[str, Any]]]:
    rankings: list[dict[str, Any]] = []
    passing: list[tuple[ComponentSpec, pd.DataFrame, dict[str, dict[str, Any]], float]] = []
    for spec in component_specs(frame):
        selected = filter_component(frame, spec)
        split = segments(selected, start, end)
        stats = {name: net_stats(part, cost_r) for name, part in split.items()}
        passed = development_pass(stats)
        score = round(component_score(stats), 6)
        rankings.append(
            {
                "spec": asdict(spec),
                "development_confirmation_passed": passed,
                "score": score,
                "segments": stats,
                "total": net_stats(selected, cost_r),
            }
        )
        if passed:
            passing.append((spec, selected, stats, score))
    rankings.sort(key=lambda item: item["score"], reverse=True)
    passing.sort(key=lambda item: item[3], reverse=True)

    chosen: list[tuple[ComponentSpec, pd.DataFrame, dict[str, dict[str, Any]], float]] = []
    existing_keys: set[tuple] = set()
    for item in passing:
        keys = trade_keys(item[1])
        if not keys:
            continue
        unique_ratio = len(keys - existing_keys) / len(keys)
        if chosen and unique_ratio < 0.20:
            continue
        chosen.append(item)
        existing_keys.update(keys)
        if len(chosen) >= MAX_COMPONENTS:
            break
    if not chosen:
        return None, pd.DataFrame(), rankings[:100]

    combined = pd.concat([item[1] for item in chosen], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(
        ["entry_time", "exit_time", "symbol", "profile", "side"]
    ).sort_values(["entry_time", "profile", "side"]).reset_index(drop=True)
    combined_segments = segments(combined, start, end)
    evidence = {name: net_stats(part, cost_r) for name, part in combined_segments.items()}
    holdout = evidence["holdout"]
    full_pass = (
        development_pass(evidence)
        and holdout["trades"] >= 5
        and holdout["net_r"] > 0
        and float(holdout["profit_factor"] or 0.0) >= 1.03
    )
    if not full_pass:
        return None, combined, rankings[:100]

    risk = base.risk_from_evidence(evidence, "ICT")
    setup = f"v14_6_1_{symbol.lower()}_ict_profile_ensemble"
    selection = base.SleeveSelection(
        symbol=symbol,
        mode="ICT",
        filter_spec=base.FilterSpec(engine=str(combined.iloc[0]["engine"])),
        base_risk_percent=risk,
        setup_name=setup,
        evidence={
            "segments": evidence,
            "total": net_stats(combined, cost_r),
            "components": [asdict(item[0]) for item in chosen],
            "selection_rule": "components chosen on development and confirmation; holdout used only for final gate",
        },
    )
    return selection, combined, rankings[:100]


def apply_ensemble(frame: pd.DataFrame, selection: base.SleeveSelection, cost_r: float) -> pd.DataFrame:
    output = frame.copy()
    output["setup"] = selection.setup_name
    output["risk_percent"] = selection.base_risk_percent
    output["sleeve_mode"] = "ICT"
    output["raw_r_multiple"] = output["r_multiple"].astype(float)
    output["cost_r"] = float(cost_r)
    output["r_multiple"] = output["raw_r_multiple"] - float(cost_r)
    return output


def daily_activity(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trading_days": 0,
            "average_entries_per_active_day": 0.0,
            "p95_entries_per_active_day": 0.0,
            "maximum_entries_per_day": 0,
            "days_with_multiple_entries": 0,
        }
    counts = pd.to_datetime(frame["entry_time"], utc=True).dt.floor("D").value_counts()
    return {
        "trading_days": int(len(counts)),
        "average_entries_per_active_day": round(float(counts.mean()), 4),
        "p95_entries_per_active_day": round(float(counts.quantile(0.95)), 4),
        "maximum_entries_per_day": int(counts.max()),
        "days_with_multiple_entries": int((counts > 1).sum()),
    }


def make_guard(symbol: str) -> SymbolGuard:
    sessions = {
        "GBPUSD": (6, 20),
        "EURUSD": (6, 19),
        "GBPJPY": (6, 20),
        "AUDUSD": (0, 18),
        "USDJPY": (0, 20),
    }
    start, end = sessions[symbol]
    multi = symbol in TARGET_SYMBOLS
    return SymbolGuard(
        post_loss_multiplier=0.65 if multi else 0.70,
        max_open_positions=2 if multi else 1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=0.90,
        stop_after_daily_losses=4 if multi else 3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=4.0 if multi else 6.0,
        win_pressure_recovery=0.70 if multi else 0.75,
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
    profit_profile.SYMBOL_GUARDS.update({symbol: make_guard(symbol) for symbol in base.SYMBOLS})
    return old_risk, old_guards


def restore_profile(old_risk: dict, old_guards: dict) -> None:
    profit_profile.SETUP_RISK_PERCENT.clear()
    profit_profile.SETUP_RISK_PERCENT.update(old_risk)
    profit_profile.SYMBOL_GUARDS.clear()
    profit_profile.SYMBOL_GUARDS.update(old_guards)


def portfolio_search(swing: pd.DataFrame, ict: pd.DataFrame):
    rows: list[dict[str, Any]] = []
    best_safe = None
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
        raise RuntimeError("No safe V14.6.1 ensemble portfolio")
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
        "# V14.6.1 Multi-Entry ICT Profile Ensemble",
        "",
        f"**Window:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        "**Starting balance:** $5,000.00",
        "**Retail-net target:** $34,000.00",
        "",
        "## Failed-symbol ICT validation",
        "",
        "| Symbol | Validated | Components | Avg entries/active day | Max entries/day |",
        "|---|---|---:|---:|---:|",
    ]
    for symbol in TARGET_SYMBOLS:
        item = payload["target_symbol_results"][symbol]
        lines.append(
            f"| {symbol} | {item['validated']} | {item['component_count']} | "
            f"{item['activity']['average_entries_per_active_day']:.2f} | "
            f"{item['activity']['maximum_entries_per_day']} |"
        )
    lines += [
        "",
        "## Safe retail portfolio comparison",
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
        "Components are selected on development and confirmation only. The final 25% holdout is used once as a promotion gate. Each selected symbol may admit one entry per hour and hold at most two ICT positions, while the 1.75% ICT and 3.25% combined portfolio caps remain active.",
        "",
        "Research only. Candidate-level R replay is not tick-level broker execution and does not guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    swing_all = load_candidates(SWING_FIXTURE)
    ict_all = load_candidates(ICT_FIXTURE)
    latest = max(swing_all["exit_time"].max(), ict_all["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    swing_window = filter_window(swing_all, start, latest)
    ict_window = filter_window(ict_all, start, latest)

    selections: dict[str, dict[str, Any]] = {}
    rankings: dict[str, Any] = {}
    selected_swing: list[pd.DataFrame] = []
    selected_ict: list[pd.DataFrame] = []
    target_results: dict[str, Any] = {}

    for symbol in base.SYMBOLS:
        selections[symbol] = {}
        rankings[symbol] = {}
        swing_symbol = swing_window[swing_window["symbol"] == symbol].copy()
        ict_symbol = ict_window[ict_window["symbol"] == symbol].copy()
        swing_selection, swing_ranking = base.select_sleeve(
            symbol, "SWING", swing_symbol, base.ALL_IN_COST_R["SWING"], start, latest
        )
        selections[symbol]["SWING"] = None if swing_selection is None else asdict(swing_selection)
        rankings[symbol]["SWING"] = swing_ranking
        if swing_selection is not None:
            selected_swing.append(base.apply_selection(swing_symbol, swing_selection))

        if symbol in TARGET_SYMBOLS:
            ict_selection, ensemble, ict_ranking = select_component_ensemble(
                symbol, ict_symbol, start, latest, INTRADAY_COST_R
            )
            selections[symbol]["ICT"] = None if ict_selection is None else asdict(ict_selection)
            rankings[symbol]["ICT"] = ict_ranking
            if ict_selection is not None:
                selected_ict.append(apply_ensemble(ensemble, ict_selection, INTRADAY_COST_R))
            components = [] if ict_selection is None else ict_selection.evidence.get("components", [])
            target_results[symbol] = {
                "validated": ict_selection is not None,
                "base_risk_percent": None if ict_selection is None else ict_selection.base_risk_percent,
                "components": components,
                "component_count": len(components),
                "evidence": None if ict_selection is None else ict_selection.evidence,
                "activity": daily_activity(ensemble),
                "candidate_count": int(len(ict_symbol)),
            }
        else:
            ict_selection, ict_ranking = base.select_sleeve(
                symbol, "ICT", ict_symbol, WIDE_COST_R, start, latest
            )
            selections[symbol]["ICT"] = None if ict_selection is None else asdict(ict_selection)
            rankings[symbol]["ICT"] = ict_ranking
            if ict_selection is not None:
                selected_ict.append(
                    base.all_in_cost(base.apply_selection(ict_symbol, ict_selection), WIDE_COST_R)
                )

    if not selected_swing or not selected_ict:
        raise RuntimeError("No validated swing or ICT portfolio")
    swing_selected = base.all_in_cost(
        pd.concat(selected_swing, ignore_index=True, sort=False),
        base.ALL_IN_COST_R["SWING"],
    )
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

    baseline = json.loads(BASE_RESULTS.read_text(encoding="utf-8"))["best_safe_portfolio"]
    active_swing = sorted(symbol for symbol in base.SYMBOLS if selections[symbol]["SWING"] is not None)
    active_ict = sorted(symbol for symbol in base.SYMBOLS if selections[symbol]["ICT"] is not None)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "retail_cost_r": {
            "SWING": base.ALL_IN_COST_R["SWING"],
            "ICT_WIDE": WIDE_COST_R,
            "ICT_INTRADAY_ENSEMBLE": INTRADAY_COST_R,
            "ICT_LEGACY_M1": base.ALL_IN_COST_R["ICT_LEGACY_M1"],
        },
        "execution_limits": {
            "target_symbol_max_open_ict_positions": 2,
            "max_entries_per_hour": 1,
            "maximum_components_per_symbol": MAX_COMPONENTS,
            "max_ict_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_ict_open_risk_percent,
            "max_combined_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_combined_open_risk_percent,
        },
        "selection_protocol": {
            "component_selection_uses_holdout": False,
            "holdout_used_for_final_gate": True,
            "development_percent": 50,
            "confirmation_percent": 25,
            "holdout_percent": 25,
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
