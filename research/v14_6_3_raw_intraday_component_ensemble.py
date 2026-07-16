"""V14.6.3 raw-candle multi-entry ICT component ensemble.

V14.6.2 validated one raw-candle ICT component for GBPJPY and AUDUSD. GBPUSD
contained several positive but individually small London/late-session
components. This study combines up to four low-overlap components per target
symbol while preserving one entry per symbol/hour.

Selection uses development and confirmation only. The final 25% is divided
into two independent chronological audit blocks, both of which must remain
positive after each row's retail cost allowance. Research only; no broker API.
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
from research import v14_6_five_symbol_dual_engine_target as base  # noqa: E402
from research import v14_6_1_intraday_ict_trend_backtest as raw  # noqa: E402
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates  # noqa: E402

OUT = ROOT / "research" / "v14_6_3_raw_intraday_output"
FIXTURE = ROOT / "research" / "v14_6_candidate_fixture"
BASE_RESULTS = ROOT / "research" / "v14_6_five_symbol_results.json"
TARGET_SYMBOLS = ("GBPUSD", "GBPJPY", "AUDUSD")
MAX_COMPONENTS = 4
MIN_UNIQUE_RATIO = 0.25


def load_fixture(name: str) -> pd.DataFrame:
    path = FIXTURE / name
    if not path.exists():
        raise FileNotFoundError(f"Missing V14.6 fixture: {path}")
    frame = pd.read_csv(path)
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["side"] = frame["side"].astype(str).str.upper()
    return frame.sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)


def four_blocks(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    span = end - start
    development_end = start + span * 0.50
    confirmation_end = start + span * 0.75
    audit_a_end = start + span * 0.875
    entry = pd.to_datetime(frame["entry_time"], utc=True)
    return {
        "development": frame[(entry >= start) & (entry < development_end)].copy(),
        "confirmation": frame[(entry >= development_end) & (entry < confirmation_end)].copy(),
        "audit_a": frame[(entry >= confirmation_end) & (entry < audit_a_end)].copy(),
        "audit_b": frame[(entry >= audit_a_end) & (entry <= end)].copy(),
    }


def mixed_stats(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "net_r": 0.0, "expectancy_r": None, "profit_factor": None}
    costs = frame["selection_cost_r"].astype(float)
    values = frame["r_multiple"].astype(float) - costs
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "expectancy_r": round(float(values.mean()), 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
    }


def component_passes_pre_audit(stats: dict[str, dict[str, Any]]) -> bool:
    development = stats["development"]
    confirmation = stats["confirmation"]
    return (
        development["trades"] >= 6
        and confirmation["trades"] >= 4
        and development["net_r"] > 0
        and confirmation["net_r"] > 0
        and float(development["profit_factor"] or 0.0) >= 1.05
        and float(confirmation["profit_factor"] or 0.0) >= 1.02
    )


def component_score(stats: dict[str, dict[str, Any]]) -> float:
    development = stats["development"]
    confirmation = stats["confirmation"]
    return (
        min(
            float(development["expectancy_r"] or -99.0),
            float(confirmation["expectancy_r"] or -99.0),
        )
        * 100.0
        + min(
            float(development["profit_factor"] or 0.0),
            float(confirmation["profit_factor"] or 0.0),
        )
        * 5.0
        + development["net_r"]
        + confirmation["net_r"]
    )


def trade_keys(frame: pd.DataFrame) -> set[tuple[str, str]]:
    return set(
        zip(
            pd.to_datetime(frame["entry_time"], utc=True).astype(str),
            frame["side"].astype(str),
        )
    )


def select_ensemble(
    symbol: str,
    candidates: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[base.SleeveSelection | None, pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    rankings: list[dict[str, Any]] = []
    passing: list[tuple[float, base.FilterSpec, pd.DataFrame, dict[str, Any]]] = []
    for spec in base.filter_candidates(candidates):
        selected = base.filter_frame(candidates, spec)
        blocks = four_blocks(selected, start, end)
        stats = {name: mixed_stats(part) for name, part in blocks.items()}
        pre_audit = component_passes_pre_audit(stats)
        score = round(component_score(stats), 6)
        rankings.append(
            {
                "spec": asdict(spec),
                "pre_audit_passed": pre_audit,
                "score": score,
                "blocks": stats,
                "total": mixed_stats(selected),
            }
        )
        if pre_audit:
            passing.append((score, spec, selected, stats))
    rankings.sort(key=lambda item: item["score"], reverse=True)
    passing.sort(key=lambda item: item[0], reverse=True)

    chosen: list[tuple[float, base.FilterSpec, pd.DataFrame, dict[str, Any]]] = []
    occupied: set[tuple[str, str]] = set()
    for item in passing:
        keys = trade_keys(item[2])
        if not keys:
            continue
        unique_ratio = len(keys - occupied) / len(keys)
        if chosen and unique_ratio < MIN_UNIQUE_RATIO:
            continue
        chosen.append(item)
        occupied.update(keys)
        if len(chosen) >= MAX_COMPONENTS:
            break

    if not chosen:
        return None, pd.DataFrame(), rankings[:100], {"reason": "NO_PRE_AUDIT_COMPONENT"}

    pieces: list[pd.DataFrame] = []
    for rank, (score, spec, selected, _) in enumerate(chosen):
        part = selected.copy()
        part["component_rank"] = rank
        part["component_score"] = score
        part["component_spec"] = json.dumps(asdict(spec), sort_keys=True)
        pieces.append(part)
    ensemble = pd.concat(pieces, ignore_index=True, sort=False)
    ensemble = ensemble.sort_values(
        ["entry_time", "component_score"], ascending=[True, False]
    ).drop_duplicates(["entry_time", "symbol", "side"])
    ensemble = ensemble.sort_values(["entry_time", "component_rank"]).reset_index(drop=True)

    blocks = four_blocks(ensemble, start, end)
    evidence = {name: mixed_stats(part) for name, part in blocks.items()}
    full_pass = (
        evidence["development"]["trades"] >= 15
        and evidence["confirmation"]["trades"] >= 8
        and evidence["audit_a"]["trades"] >= 3
        and evidence["audit_b"]["trades"] >= 3
        and all(evidence[name]["net_r"] > 0 for name in evidence)
        and all(float(evidence[name]["profit_factor"] or 0.0) >= 1.03 for name in evidence)
    )
    diagnostics = {
        "full_pass": full_pass,
        "components": [asdict(item[1]) for item in chosen],
        "component_count": len(chosen),
        "blocks": evidence,
        "total": mixed_stats(ensemble),
    }
    if not full_pass:
        return None, ensemble, rankings[:100], diagnostics

    risk_evidence = {
        "development": evidence["development"],
        "confirmation": evidence["confirmation"],
        "holdout": {
            "trades": evidence["audit_a"]["trades"] + evidence["audit_b"]["trades"],
            "net_r": round(evidence["audit_a"]["net_r"] + evidence["audit_b"]["net_r"], 6),
            "expectancy_r": round(
                (
                    evidence["audit_a"]["net_r"] + evidence["audit_b"]["net_r"]
                )
                / max(1, evidence["audit_a"]["trades"] + evidence["audit_b"]["trades"]),
                6,
            ),
            "profit_factor": min(
                float(evidence["audit_a"]["profit_factor"] or 0.0),
                float(evidence["audit_b"]["profit_factor"] or 0.0),
            ),
        },
    }
    risk = base.risk_from_evidence(risk_evidence, "ICT")
    setup = f"v14_6_3_{symbol.lower()}_ict_component_ensemble"
    selection = base.SleeveSelection(
        symbol=symbol,
        mode="ICT",
        filter_spec=chosen[0][1],
        base_risk_percent=risk,
        setup_name=setup,
        evidence={
            "four_blocks": evidence,
            "total": mixed_stats(ensemble),
            "components": diagnostics["components"],
            "selection_uses_audit": False,
        },
    )
    return selection, ensemble, rankings[:100], diagnostics


def apply_ensemble(frame: pd.DataFrame, selection: base.SleeveSelection) -> pd.DataFrame:
    output = frame.copy()
    output["setup"] = selection.setup_name
    output["risk_percent"] = selection.base_risk_percent
    output["sleeve_mode"] = "ICT"
    output["raw_r_multiple"] = output["r_multiple"].astype(float)
    output["cost_r"] = output["selection_cost_r"].astype(float)
    output["r_multiple"] = output["raw_r_multiple"] - output["cost_r"]
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
        "# V14.6.3 Raw-Candle Multi-Entry ICT Component Ensemble",
        "",
        f"**Window:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        "**Starting balance:** $5,000.00",
        "**Retail-net target:** $34,000.00",
        "",
        "## Target-symbol validation",
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
        "## Safe portfolio comparison",
        "",
        "| Metric | V14.6 | V14.6.3 |",
        "|---|---:|---:|",
        f"| Net profit | ${baseline['net_profit']:,.2f} | ${best['net_profit']:,.2f} |",
        f"| Ending balance | ${baseline['ending_balance']:,.2f} | ${best['ending_balance']:,.2f} |",
        f"| Profit factor | {baseline['profit_factor']:.4f} | {best['profit_factor']:.4f} |",
        f"| Closed drawdown | {baseline['max_closed_drawdown_percent']:.4f}% | {best['max_closed_drawdown_percent']:.4f}% |",
        f"| Stress drawdown | {baseline['stress_drawdown_percent']:.4f}% | {best['stress_drawdown_percent']:.4f}% |",
        f"| Target reached | False | {payload['target_reached']} |",
        "",
        "Components were ranked using development and confirmation only. Audit A and Audit B were both required to remain positive. One entry per symbol/hour, two simultaneous target-symbol ICT positions, the 1.75% ICT cap, 3.25% combined cap and original drawdown governor were retained.",
        "",
        "Research only. Historical performance does not guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    swing_all = load_fixture("all_swing_candidates.csv")
    wide_all = load_fixture("all_wide_ict_candidates.csv")
    intraday_all = raw.build_intraday_candidates()
    intraday_all.to_csv(OUT / "all_intraday_ict_candidates.csv", index=False)

    latest = max(swing_all["exit_time"].max(), wide_all["exit_time"].max(), intraday_all["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    swing_window = filter_window(swing_all, start, latest)
    wide_window = filter_window(wide_all, start, latest).copy()
    intraday_window = filter_window(intraday_all, start, latest).copy()
    wide_window["selection_cost_r"] = base.ALL_IN_COST_R["ICT_WIDE"]
    wide_window["cost_class"] = "ICT_WIDE"
    intraday_window["selection_cost_r"] = raw.INTRADAY_COST_R
    intraday_window["cost_class"] = "ICT_INTRADAY"
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
        selections[symbol]["SWING"] = None if swing_selection is None else asdict(swing_selection)
        rankings[symbol]["SWING"] = swing_ranking
        if swing_selection is not None:
            selected_swing.append(base.apply_selection(swing_symbol, swing_selection))

        if symbol in TARGET_SYMBOLS:
            ict_selection, ensemble, ict_ranking, diagnostic = select_ensemble(
                symbol, ict_symbol, start, latest
            )
            selections[symbol]["ICT"] = None if ict_selection is None else asdict(ict_selection)
            rankings[symbol]["ICT"] = ict_ranking
            if ict_selection is not None:
                selected_ict.append(apply_ensemble(ensemble, ict_selection))
            target_results[symbol] = {
                "validated": ict_selection is not None,
                "base_risk_percent": None if ict_selection is None else ict_selection.base_risk_percent,
                "component_count": diagnostic.get("component_count", 0),
                "components": diagnostic.get("components", []),
                "evidence": diagnostic.get("blocks"),
                "total": diagnostic.get("total"),
                "activity": daily_activity(ensemble),
                "candidate_count": int(len(ict_symbol)),
                "intraday_candidate_count": int(len(intraday_window[intraday_window["symbol"] == symbol])),
                "diagnostic": diagnostic,
            }
        else:
            ict_selection, ict_ranking = raw.select_mixed_cost_ict(symbol, ict_symbol, start, latest)
            selections[symbol]["ICT"] = None if ict_selection is None else asdict(ict_selection)
            rankings[symbol]["ICT"] = ict_ranking
            if ict_selection is not None:
                selected_ict.append(raw.apply_mixed_selection(ict_symbol, ict_selection))

    if not selected_swing or not selected_ict:
        raise RuntimeError("No validated V14.6.3 portfolio")
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
            lambda row: f"v14_6_3_legacy_{str(row['symbol']).lower()}_{str(row['setup']).lower()}",
            axis=1,
        )
        legacy["risk_percent"] = base.OBSERVATION_RISK
        legacy = base.all_in_cost(legacy, base.ALL_IN_COST_R["ICT_LEGACY_M1"])
        ict_selected = pd.concat([ict_selected, legacy], ignore_index=True, sort=False)

    best_safe, search_rows, best_trades, best_skipped, best_events = raw.portfolio_search(
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
            "ICT_WIDE": base.ALL_IN_COST_R["ICT_WIDE"],
            "ICT_INTRADAY": raw.INTRADAY_COST_R,
            "ICT_LEGACY_M1": base.ALL_IN_COST_R["ICT_LEGACY_M1"],
        },
        "selection_protocol": {
            "development_percent": 50.0,
            "confirmation_percent": 25.0,
            "audit_a_percent": 12.5,
            "audit_b_percent": 12.5,
            "component_selection_uses_audits": False,
            "both_audits_required_positive": True,
            "max_components_per_symbol": MAX_COMPONENTS,
        },
        "execution_limits": {
            "target_symbol_max_open_ict_positions": 2,
            "max_entries_per_hour": 1,
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
    (OUT / "v14_6_3_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
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
