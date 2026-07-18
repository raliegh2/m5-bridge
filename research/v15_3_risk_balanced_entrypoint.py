"""V15.3 jointly safe risk-balanced multi-system backtest.

Uses the frozen V15.1 profile-selection rules, but selects the new-system risk
multiplier only when it is safe in both the 2019-2026 capacity replay and the
separate 2024-2026 deployment replay.  Core V14.9 risk is unchanged.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v15_1_currency_factor_entrypoint as model  # noqa: E402
from research import v15_diversified_target_backtest as v15  # noqa: E402

OUT = ROOT / "research" / "v15_3_risk_balanced_output"
RISK_MULTIPLIERS = (0.25, 0.35, 0.45, 0.55, 0.60, 0.65, 0.70)
TARGET_20K = 20_000.0
TARGET_40K = 40_000.0


def safe(summary: dict[str, Any]) -> bool:
    return (
        float(summary["max_closed_drawdown_percent"]) <= 9.60
        and float(summary["stress_drawdown_percent"]) <= 10.00
    )


def stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v15.dollar_stats(frame)


def attribution(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {}
    return {str(key): stats(group) for key, group in frame.groupby(column, dropna=False)}


def run_grid(
    baseline: pd.DataFrame,
    new_source: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[float, tuple]]:
    rows: list[dict[str, Any]] = []
    outputs: dict[float, tuple] = {}
    for multiplier in RISK_MULTIPLIERS:
        admitted = v15.unified_admission(baseline, new_source, multiplier)
        summary, trades, skipped, replay, active = v15.replay_from_admission(admitted)
        new_trades = trades[trades.get("priority_class", 0) == 1].copy()
        new_stats = stats(new_trades)
        rows.append(
            {
                "risk_multiplier": float(multiplier),
                **summary,
                "safe": safe(summary),
                "new_system_trades": new_stats["trades"],
                "new_system_net_profit": new_stats["net_profit"],
                "new_system_profit_factor": new_stats["profit_factor"],
                "new_candidates": int((active.priority_class == 1).sum()),
            }
        )
        outputs[float(multiplier)] = (summary, trades, skipped, replay, active, admitted)
    return pd.DataFrame(rows), outputs


def write_report(payload: dict[str, Any]) -> None:
    base = payload["baseline_v14_9"]
    portfolio = payload["portfolio"]
    forward = payload["forward_deployment_portfolio"]
    new = payload["new_system_contribution"]
    holdout = payload["forward_2024_2026_combined"]
    lines = [
        "# V15.3 Risk-Balanced Multi-System Portfolio", "",
        "**Data:** FXCM official H1 bid/ask archive",
        "**Starting balance:** $5,000.00",
        "**Database window:** 2016 through the latest common 2026 candle", "",
        "## Ten-year capacity replay", "",
        "| Metric | V14.9 | V15.3 | Change |", "|---|---:|---:|---:|",
        f"| Net profit | ${base['net_profit']:,.2f} | ${portfolio['net_profit']:,.2f} | ${portfolio['net_profit']-base['net_profit']:,.2f} |",
        f"| Ending balance | ${base['ending_balance']:,.2f} | ${portfolio['ending_balance']:,.2f} | ${portfolio['ending_balance']-base['ending_balance']:,.2f} |",
        f"| Profit factor | {float(base['profit_factor'] or 0):.4f} | {float(portfolio['profit_factor'] or 0):.4f} | {float(portfolio['profit_factor'] or 0)-float(base['profit_factor'] or 0):.4f} |",
        f"| Max closed drawdown | {base['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent']-base['max_closed_drawdown_percent']:.4f} pp |",
        f"| Stressed drawdown | {base['stress_drawdown_percent']:.4f}% | {portfolio['stress_drawdown_percent']:.4f}% | {portfolio['stress_drawdown_percent']-base['stress_drawdown_percent']:.4f} pp |",
        f"| Closed trades | {base['closed_trades']} | {portfolio['closed_trades']} | {portfolio['closed_trades']-base['closed_trades']} |", "",
        f"Selected new-system multiplier: **{payload['selected_risk_multiplier']:.2f}x**.", "",
        "## New systems", "",
        f"- Net contribution: **${new['net_profit']:,.2f}**",
        f"- Profit factor: **{float(new['profit_factor'] or 0):.4f}**",
        f"- Closed trades: **{new['trades']}**", "",
        "## Separate 2024-2026 deployment replay", "",
        f"- Portfolio net profit: **${forward['net_profit']:,.2f}**",
        f"- Portfolio PF: **{float(forward['profit_factor'] or 0):.4f}**",
        f"- Max closed drawdown: **{forward['max_closed_drawdown_percent']:.4f}%**",
        f"- Stressed drawdown: **{forward['stress_drawdown_percent']:.4f}%**",
        f"- 2024-2026 combined trade net: **${holdout['net_profit']:,.2f}**", "",
        "## Targets", "",
        f"- $20,000 target reached: **{payload['target_20k_reached']}**; gap **${payload['target_20k_gap']:,.2f}**.",
        f"- $40,000 target reached: **{payload['target_40k_reached']}**; gap **${payload['target_40k_gap']:,.2f}**.", "",
        "Research only. Core V14.9 risk and the 7.5/8.5/9.0/9.6 drawdown governor remain unchanged. No live execution code was modified.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    model.OUT = OUT
    raw, core, quality = v15.load_market()
    baseline, baseline_evidence = v15.build_baseline_candidates(core)
    baseline_summary, baseline_trades = v15.baseline_replay(baseline)
    baseline_trades.to_csv(OUT / "baseline_v14_9_closed_trades.csv", index=False)

    allowed = sorted(set(raw) - set(v15.CORE_SYMBOLS))
    legacy = v15.diversified.generate_universe_candidates(raw, allowed)
    factor = model.systems.generate_all_candidates(raw, allowed)
    frames = [item for item in (legacy, factor) if item is not None and not item.empty]
    if not frames:
        raise RuntimeError("V15.3 generated no new-system candidates")
    source = pd.concat(frames, ignore_index=True, sort=False)
    source["entry_time"] = pd.to_datetime(source.entry_time, utc=True)
    source["exit_time"] = pd.to_datetime(source.exit_time, utc=True)
    source = source[(source.entry_time >= model.START) & (source.entry_time <= model.END)].copy()
    source["profile"] = source.profile.astype(str)
    source["family"] = source.family.astype(str)
    source["setup"] = "v15_3_" + source.symbol.astype(str).str.lower() + "_" + source.profile.str.lower()
    source["sleeve_id"] = source.symbol.astype(str) + "/" + source.family + "/" + source.profile
    source["priority_class"] = 1
    source.to_csv(OUT / "all_v15_3_candidates.csv", index=False)

    evidence, selected = model.build_evidence(source)
    selected_ids = set(selected.sleeve_id)
    model.flatten_evidence(evidence, selected_ids).to_csv(OUT / "profile_evidence.csv", index=False)
    (OUT / "selected_profiles.json").write_text(
        json.dumps(model.selected_records(selected), indent=2, default=str), encoding="utf-8"
    )

    capacity_source = model.materialize(source, selected, model.FIT_START)
    deployment_source = model.materialize(source, selected, model.HOLDOUT_START)
    capacity_source.to_csv(OUT / "selected_capacity_candidates.csv", index=False)
    deployment_source.to_csv(OUT / "selected_forward_candidates.csv", index=False)

    capacity_grid, capacity_outputs = run_grid(baseline, capacity_source)
    forward_grid, forward_outputs = run_grid(baseline, deployment_source)
    capacity_grid.to_csv(OUT / "capacity_risk_grid.csv", index=False)
    forward_grid.to_csv(OUT / "forward_risk_grid.csv", index=False)

    joined = capacity_grid[
        ["risk_multiplier", "safe", "net_profit", "new_system_net_profit", "new_system_profit_factor"]
    ].merge(
        forward_grid[["risk_multiplier", "safe", "net_profit"]],
        on="risk_multiplier", suffixes=("_capacity", "_forward")
    )
    eligible = joined[
        joined.safe_capacity
        & joined.safe_forward
        & (joined.new_system_net_profit > 0.0)
        & (joined.new_system_profit_factor.fillna(0.0) > 1.0)
    ].copy()
    if eligible.empty:
        raise RuntimeError(f"No jointly safe V15.3 allocation: {joined.to_dict('records')}")
    selected_multiplier = float(
        eligible.sort_values(["net_profit_capacity", "new_system_net_profit"], ascending=False).iloc[0].risk_multiplier
    )

    portfolio, trades, skipped, replay, active, admitted = capacity_outputs[selected_multiplier]
    forward, forward_trades, forward_skipped, forward_replay, forward_active, forward_admitted = forward_outputs[selected_multiplier]
    if not safe(portfolio) or not safe(forward):
        raise RuntimeError("Selected V15.3 allocation violated a drawdown boundary")

    admitted.to_csv(OUT / "capacity_gate_and_admission.csv", index=False)
    forward_admitted.to_csv(OUT / "forward_gate_and_admission.csv", index=False)
    trades.to_csv(OUT / "closed_trades.csv", index=False)
    forward_trades.to_csv(OUT / "forward_closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    forward_skipped.to_csv(OUT / "forward_skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(OUT / "closed_drawdown_governor_events.csv", index=False)
    pd.DataFrame(replay.projected_stress_events).to_csv(OUT / "projected_stress_governor_events.csv", index=False)

    old_out, old_start, old_end = v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END
    v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = OUT, v15.TEST_START, model.END
    try:
        monthly, annual = v15.external.time_series(trades)
        monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
        annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
        v15.external.plot_outputs(monthly, annual, trades)
    finally:
        v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = old_out, old_start, old_end

    new_trades = trades[trades.get("priority_class", 0) == 1].copy()
    holdout = forward_trades[pd.to_datetime(forward_trades.entry_time, utc=True) >= model.HOLDOUT_START].copy()
    target20 = float(portfolio["net_profit"]) >= TARGET_20K
    target40 = float(portfolio["net_profit"]) >= TARGET_40K
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "FXCM official weekly H1 bid/ask archive",
        "database_window": {"start": model.START.isoformat(), "end": model.END.isoformat()},
        "selection_window": {"start": model.START.isoformat(), "end": (model.HOLDOUT_START-pd.Timedelta(seconds=1)).isoformat()},
        "forward_window": {"start": model.HOLDOUT_START.isoformat(), "end": model.END.isoformat()},
        "selected_risk_multiplier": selected_multiplier,
        "risk_selection": {
            "capacity_and_forward_safety_required": True,
            "risk_multiplier_grid": list(RISK_MULTIPLIERS),
            "maximum_new_trade_percent_before_multiplier": v15.MAX_NEW_TRADE_RISK,
            "maximum_new_open_risk_percent": v15.MAX_NEW_OPEN_RISK,
            "maximum_combined_open_risk_percent": v15.MAX_COMBINED_OPEN_RISK,
            "maximum_closed_drawdown_percent": 9.60,
            "maximum_stressed_drawdown_percent": 10.00,
        },
        "data_quality": quality,
        "baseline_sleeve_evidence": baseline_evidence,
        "selected_profiles": model.selected_records(selected),
        "baseline_v14_9": baseline_summary,
        "portfolio": {**portfolio, "safe": True},
        "forward_deployment_portfolio": {**forward, "safe": True},
        "new_system_contribution": stats(new_trades),
        "forward_2024_2026_combined": stats(holdout),
        "attribution_by_symbol": attribution(trades, "symbol"),
        "attribution_by_family": attribution(trades, "family"),
        "capacity_risk_grid": capacity_grid.to_dict("records"),
        "forward_risk_grid": forward_grid.to_dict("records"),
        "target_20k_reached": target20,
        "target_20k_gap": round(max(0.0, TARGET_20K-float(portfolio["net_profit"])), 2),
        "target_40k_reached": target40,
        "target_40k_gap": round(max(0.0, TARGET_40K-float(portfolio["net_profit"])), 2),
        "research_only": True,
        "live_execution_changed": False,
    }
    (OUT / "v15_3_risk_balanced_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(payload)
    print(json.dumps({
        "baseline": baseline_summary,
        "selected_risk_multiplier": selected_multiplier,
        "portfolio": portfolio,
        "forward_deployment": forward,
        "new_systems": payload["new_system_contribution"],
        "forward_2024_2026": payload["forward_2024_2026_combined"],
        "target_20k": target20,
        "target_20k_gap": payload["target_20k_gap"],
        "target_40k": target40,
        "target_40k_gap": payload["target_40k_gap"],
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
