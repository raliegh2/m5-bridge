"""V15.2 safe ten-year diversified portfolio replay.

This entry point keeps V14.9 swing/ICT definitions and V15.1 profile selection
unchanged. It searches a pre-registered bounded allocation grid and selects the
highest-profit configuration that remains inside the original 9.6% closed and
10% stressed drawdown limits. The 2024-2026 segment remains excluded from
profile selection.
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

from mt5_ai_bridge import v15_1_currency_systems as systems  # noqa: E402
from research import v15_1_currency_factor_entrypoint as base  # noqa: E402
from research import v15_diversified_target_backtest as v15  # noqa: E402

OUT = ROOT / "research" / "v15_2_safe_ten_year_output"
MULTIPLIERS = (0.30, 0.40, 0.50, 0.55, 0.60, 0.65, 0.70)
TARGET_20K = 20_000.0
TARGET_40K = 40_000.0


def stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v15.dollar_stats(frame)


def attribution(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {}
    return {str(key): stats(group) for key, group in frame.groupby(column, dropna=False)}


def replay_grid(baseline: pd.DataFrame, source: pd.DataFrame) -> tuple[pd.DataFrame, dict[float, tuple]]:
    rows: list[dict[str, Any]] = []
    outputs: dict[float, tuple] = {}
    for multiplier in MULTIPLIERS:
        admitted = v15.unified_admission(baseline, source, multiplier)
        summary, trades, skipped, replay, active = v15.replay_from_admission(admitted)
        safe = (
            float(summary["max_closed_drawdown_percent"]) <= 9.60
            and float(summary["stress_drawdown_percent"]) <= 10.00
        )
        rows.append(
            {
                "risk_multiplier": multiplier,
                **summary,
                "safe": safe,
                "new_candidates": int((active.priority_class == 1).sum()),
            }
        )
        outputs[multiplier] = (summary, trades, skipped, replay, active, admitted)
    return pd.DataFrame(rows), outputs


def choose_primary(grid: pd.DataFrame) -> float:
    safe = grid[grid.safe == True].copy()  # noqa: E712
    if safe.empty:
        raise RuntimeError(f"No safe V15.2 allocation in bounded grid: {grid.to_dict('records')}")
    safe = safe.sort_values(["net_profit", "profit_factor", "risk_multiplier"], ascending=[False, False, True])
    return float(safe.iloc[0].risk_multiplier)


def write_report(payload: dict[str, Any]) -> None:
    baseline = payload["baseline_v14_9"]
    portfolio = payload["portfolio"]
    holdout = payload["holdout_2024_2026"]
    new = payload["new_system_contribution"]
    lines = [
        "# V15.2 Safe Diversified Ten-Year Backtest",
        "",
        "**Data:** FXCM official weekly H1 bid/ask archive",
        "**Starting balance:** $5,000.00",
        "**Historical window:** 2016-01-01 through latest common 2026 FXCM candle",
        f"**Selected bounded allocation multiplier:** {payload['selected_risk_multiplier']:.2f}x",
        "",
        "## Portfolio result",
        "",
        "| Metric | V14.9 | V15.2 | Change |",
        "|---|---:|---:|---:|",
        f"| Net profit | ${baseline['net_profit']:,.2f} | ${portfolio['net_profit']:,.2f} | ${portfolio['net_profit']-baseline['net_profit']:,.2f} |",
        f"| Ending balance | ${baseline['ending_balance']:,.2f} | ${portfolio['ending_balance']:,.2f} | ${portfolio['ending_balance']-baseline['ending_balance']:,.2f} |",
        f"| Profit factor | {float(baseline['profit_factor'] or 0):.4f} | {float(portfolio['profit_factor'] or 0):.4f} | {float(portfolio['profit_factor'] or 0)-float(baseline['profit_factor'] or 0):.4f} |",
        f"| Closed drawdown | {baseline['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent']-baseline['max_closed_drawdown_percent']:.4f} pp |",
        f"| Stressed drawdown | {baseline['stress_drawdown_percent']:.4f}% | {portfolio['stress_drawdown_percent']:.4f}% | {portfolio['stress_drawdown_percent']-baseline['stress_drawdown_percent']:.4f} pp |",
        f"| Closed trades | {baseline['closed_trades']} | {portfolio['closed_trades']} | {portfolio['closed_trades']-baseline['closed_trades']} |",
        "",
        "## 2024-2026 chronological check",
        "",
        f"- Combined net profit: **${holdout['net_profit']:,.2f}**",
        f"- Combined profit factor: **{float(holdout['profit_factor'] or 0):.4f}**",
        f"- New-system net contribution: **${new['net_profit']:,.2f}**",
        f"- New-system profit factor: **{float(new['profit_factor'] or 0):.4f}**",
        "",
        "## Targets",
        "",
        f"- $20,000 target reached: **{payload['target_20k_reached']}**; gap ${payload['target_20k_gap']:,.2f}.",
        f"- $40,000 target reached: **{payload['target_40k_reached']}**; gap ${payload['target_40k_gap']:,.2f}.",
        "",
        "Research only. No MT5, broker-order, AUTO, or live-runner code was changed.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw, core, quality = v15.load_market()
    baseline, baseline_evidence = v15.build_baseline_candidates(core)
    baseline_summary, baseline_trades = v15.baseline_replay(baseline)
    baseline_trades.to_csv(OUT / "baseline_v14_9_closed_trades.csv", index=False)

    allowed = sorted(set(raw) - set(v15.CORE_SYMBOLS))
    old = v15.diversified.generate_universe_candidates(raw, allowed)
    fresh = systems.generate_all_candidates(raw, allowed)
    frames = [frame for frame in (old, fresh) if frame is not None and not frame.empty]
    if not frames:
        raise RuntimeError("V15.2 generated no diversified candidates")
    source = pd.concat(frames, ignore_index=True, sort=False)
    source["entry_time"] = pd.to_datetime(source.entry_time, utc=True)
    source["exit_time"] = pd.to_datetime(source.exit_time, utc=True)
    source = source[(source.entry_time >= base.START) & (source.entry_time <= base.END)]
    source["profile"] = source.profile.astype(str)
    source["family"] = source.family.astype(str)
    source["setup"] = "v15_2_" + source.symbol.astype(str).str.lower() + "_" + source.profile.str.lower()
    source["sleeve_id"] = source.symbol.astype(str) + "/" + source.family + "/" + source.profile
    source["priority_class"] = 1
    source.to_csv(OUT / "all_v15_2_candidates.csv", index=False)

    evidence, selected = base.build_evidence(source)
    selected_ids = set(selected.sleeve_id)
    base.flatten_evidence(evidence, selected_ids).to_csv(OUT / "profile_evidence.csv", index=False)
    (OUT / "selected_profiles.json").write_text(
        json.dumps(base.selected_records(selected), indent=2, default=str), encoding="utf-8"
    )

    fitted_source = base.materialize(source, selected, base.FIT_START)
    fitted_source.to_csv(OUT / "selected_candidates.csv", index=False)
    grid, outputs = replay_grid(baseline, fitted_source)
    grid.to_csv(OUT / "bounded_risk_grid.csv", index=False)
    selected_multiplier = choose_primary(grid)
    summary, trades, skipped, replay, active, admitted = outputs[selected_multiplier]

    if float(summary["max_closed_drawdown_percent"]) > 9.60:
        raise RuntimeError(f"Selected V15.2 allocation exceeds closed drawdown: {summary}")
    if float(summary["stress_drawdown_percent"]) > 10.00:
        raise RuntimeError(f"Selected V15.2 allocation exceeds stressed drawdown: {summary}")

    admitted.to_csv(OUT / "gate_and_admission.csv", index=False)
    trades.to_csv(OUT / "closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(OUT / "closed_drawdown_governor_events.csv", index=False)
    pd.DataFrame(replay.projected_stress_events).to_csv(OUT / "projected_stress_governor_events.csv", index=False)

    holdout = trades[pd.to_datetime(trades.entry_time, utc=True) >= base.HOLDOUT_START].copy()
    new_trades = trades[trades.get("priority_class", 0) == 1].copy()
    new_holdout = new_trades[pd.to_datetime(new_trades.entry_time, utc=True) >= base.HOLDOUT_START].copy()

    old_out, old_start, old_end = v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END
    v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = OUT, v15.TEST_START, base.END
    try:
        monthly, annual = v15.external.time_series(trades)
        monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
        annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
        v15.external.plot_outputs(monthly, annual, trades)
    finally:
        v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = old_out, old_start, old_end

    target20 = float(summary["net_profit"]) >= TARGET_20K
    target40 = float(summary["net_profit"]) >= TARGET_40K
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "FXCM official weekly H1 bid/ask archive",
        "test_window": {"start": base.START.isoformat(), "end": base.END.isoformat()},
        "profile_selection_window": {"start": base.START.isoformat(), "end": (base.HOLDOUT_START-pd.Timedelta(seconds=1)).isoformat()},
        "holdout_window": {"start": base.HOLDOUT_START.isoformat(), "end": base.END.isoformat()},
        "selection_protocol": {
            "holdout_used_for_profile_selection": False,
            "bounded_allocation_grid": list(MULTIPLIERS),
            "selected_for_highest_safe_historical_net_profit": True,
            "untouched_out_of_sample_claimed_for_allocation": False,
        },
        "selected_risk_multiplier": selected_multiplier,
        "data_quality": quality,
        "baseline_sleeve_evidence": baseline_evidence,
        "selected_profiles": base.selected_records(selected),
        "baseline_v14_9": baseline_summary,
        "portfolio": {**summary, "safe": True},
        "holdout_2024_2026": stats(holdout),
        "new_system_contribution": stats(new_trades),
        "new_system_holdout_2024_2026": stats(new_holdout),
        "attribution_by_family": attribution(trades, "family"),
        "attribution_by_symbol": attribution(trades, "symbol"),
        "bounded_risk_grid": grid.to_dict("records"),
        "target_20k_reached": target20,
        "target_20k_gap": round(max(0.0, TARGET_20K-float(summary["net_profit"])), 2),
        "target_40k_reached": target40,
        "target_40k_gap": round(max(0.0, TARGET_40K-float(summary["net_profit"])), 2),
        "risk_limits": {
            "maximum_closed_drawdown_percent": 9.60,
            "maximum_stressed_drawdown_percent": 10.00,
            "maximum_new_trade_percent": v15.MAX_NEW_TRADE_RISK,
            "maximum_new_open_risk_percent": v15.MAX_NEW_OPEN_RISK,
            "maximum_combined_open_risk_percent": v15.MAX_COMBINED_OPEN_RISK,
        },
        "research_only": True,
        "live_execution_changed": False,
    }
    (OUT / "v15_2_safe_ten_year_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(payload)
    print(json.dumps({
        "selected_multiplier": selected_multiplier,
        "baseline": baseline_summary,
        "portfolio": summary,
        "holdout_2024_2026": payload["holdout_2024_2026"],
        "new_system_contribution": payload["new_system_contribution"],
        "new_system_holdout": payload["new_system_holdout_2024_2026"],
        "targets": {
            "20k": target20, "20k_gap": payload["target_20k_gap"],
            "40k": target40, "40k_gap": payload["target_40k_gap"],
        },
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
