"""V15.1 frozen currency-factor/session research with a 2024-2026 holdout."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from mt5_ai_bridge import v15_1_currency_systems as systems  # noqa: E402
from research import v15_diversified_target_backtest as v15  # noqa: E402

OUT = ROOT / "research" / "v15_1_currency_factor_output"
START = pd.Timestamp("2016-01-01T00:00:00Z")
TRAIN_END = pd.Timestamp("2019-01-01T00:00:00Z")
VALIDATION_END = pd.Timestamp("2021-01-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2024-01-01T00:00:00Z")
END = v15.TEST_END
FIT_START = pd.Timestamp("2019-01-01T00:00:00Z")
RISK_MULTIPLIERS = (0.70, 0.85, 1.00)
TARGET_20K = 20_000.0
TARGET_40K = 40_000.0
MAX_SLEEVES = 18


def blocks(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        "training": v15.ratio_stats(frame[(frame.entry_time >= START) & (frame.entry_time < TRAIN_END)]),
        "validation": v15.ratio_stats(frame[(frame.entry_time >= TRAIN_END) & (frame.entry_time < VALIDATION_END)]),
        "audit": v15.ratio_stats(frame[(frame.entry_time >= VALIDATION_END) & (frame.entry_time < HOLDOUT_START)]),
    }


def minima(family: str) -> tuple[int, int, int]:
    if family in {"D1_TREND", "D1_SQUEEZE", "D1_REVERSION"} or family.startswith("CURRENCY_FACTOR"):
        return 4, 3, 4
    if family in {"SESSION_BREAKOUT", "SESSION_FADE", "LIQUIDITY_FADE"}:
        return 18, 10, 14
    return 10, 6, 8


def max_drawdown_r(frame: pd.DataFrame) -> float:
    values = pd.to_numeric(frame.r_multiple, errors="coerce").dropna()
    if values.empty:
        return 0.0
    curve = values.cumsum()
    return float((curve.cummax().clip(lower=0.0) - curve).max())


def build_evidence(source: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for sleeve_id, group in source.groupby("sleeve_id", sort=False):
        family = str(group.iloc[0].family)
        evidence = blocks(group)
        development_frame = group[(group.entry_time >= START) & (group.entry_time < HOLDOUT_START)]
        holdout_frame = group[(group.entry_time >= HOLDOUT_START) & (group.entry_time <= END)]
        development = v15.ratio_stats(development_frame)
        holdout = v15.ratio_stats(holdout_frame)
        development["maximum_drawdown_r"] = round(max_drawdown_r(development_frame), 6)
        positive = sum(float(item["net_r"]) > 0 for item in evidence.values())
        pf_blocks = sum(float(item["profit_factor"] or 0.0) >= 1.0 for item in evidence.values())
        minimum = minima(family)
        enough = all(int(evidence[name]["trades"]) >= count for name, count in zip(("training", "validation", "audit"), minimum))
        audit = evidence["audit"]
        robust_score = (
            float(development["expectancy_r"] or 0.0)
            * math.sqrt(max(1, int(development["trades"])))
            * min(3.0, float(development["profit_factor"] or 0.0))
            / max(1.0, float(development["maximum_drawdown_r"]))
        )
        passed = (
            enough
            and positive >= 2
            and pf_blocks >= 2
            and float(development["net_r"]) >= 3.0
            and float(development["profit_factor"] or 0.0) >= 1.20
            and float(development["maximum_drawdown_r"]) <= 8.0
            and float(audit["net_r"]) > 0.0
            and float(audit["profit_factor"] or 0.0) >= 1.05
        )
        record = {
            "sleeve_id": sleeve_id,
            "symbol": str(group.iloc[0].symbol),
            "family": family,
            "profile": str(group.iloc[0].profile),
            "timeframe": str(group.iloc[0].timeframe),
            "training": evidence["training"],
            "validation": evidence["validation"],
            "audit": audit,
            "development": development,
            "holdout_shadow": holdout,
            "positive_blocks": positive,
            "profitable_pf_blocks": pf_blocks,
            "robust_score": robust_score,
            "passed": passed,
        }
        rows.append(record)
        if passed:
            pf = float(development["profit_factor"] or 0.0)
            net = float(development["net_r"])
            risk = 0.25
            if pf >= 1.40 and net >= 5.0:
                risk = 0.35
            if pf >= 1.75 and net >= 8.0 and float(development["maximum_drawdown_r"]) <= 5.5:
                risk = 0.50
            accepted.append({**record, "risk_percent": risk})
    evidence_frame = pd.DataFrame(rows)
    if not accepted:
        raise RuntimeError("No V15.1 profile passed frozen 2016-2023 selection")
    selected = pd.DataFrame(accepted).sort_values("robust_score", ascending=False)
    selected = selected.drop_duplicates(["symbol", "family"], keep="first").head(MAX_SLEEVES).reset_index(drop=True)
    return evidence_frame, selected


def flatten_evidence(evidence: pd.DataFrame, selected_ids: set[str]) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for item in evidence.to_dict("records"):
        row = {
            "sleeve_id": item["sleeve_id"], "symbol": item["symbol"], "family": item["family"],
            "profile": item["profile"], "timeframe": item["timeframe"],
            "selected": item["sleeve_id"] in selected_ids, "passed": item["passed"],
            "positive_blocks": item["positive_blocks"], "profitable_pf_blocks": item["profitable_pf_blocks"],
            "robust_score": item["robust_score"],
        }
        for section in ("training", "validation", "audit", "development", "holdout_shadow"):
            row.update({f"{section}_{key}": value for key, value in item[section].items()})
        output.append(row)
    return pd.DataFrame(output)


def materialize(source: pd.DataFrame, selected: pd.DataFrame, deployment_start: pd.Timestamp) -> pd.DataFrame:
    risk = dict(zip(selected.sleeve_id, selected.risk_percent))
    score = dict(zip(selected.sleeve_id, selected.robust_score))
    frame = source[source.sleeve_id.isin(set(selected.sleeve_id))].copy()
    frame = frame[(frame.entry_time >= deployment_start) & (frame.entry_time <= END)]
    frame["risk_percent"] = frame.sleeve_id.map(risk).astype(float)
    frame["requested_risk_percent"] = frame.risk_percent
    frame["priority_score"] = frame.sleeve_id.map(score).astype(float)
    frame["priority_class"] = 1
    frame["gate_active"] = True
    frame["gate_reason"] = "V15_1_FROZEN_2016_2023_PROFILE"
    return frame.sort_values(["entry_time", "priority_score"], ascending=[True, False]).reset_index(drop=True)


def run_grid(baseline: pd.DataFrame, new_source: pd.DataFrame) -> tuple[pd.DataFrame, dict[float, tuple]]:
    rows, outputs = [], {}
    for multiplier in RISK_MULTIPLIERS:
        admitted = v15.unified_admission(baseline, new_source, multiplier)
        summary, trades, skipped, replay, active = v15.replay_from_admission(admitted)
        safe = float(summary["max_closed_drawdown_percent"]) <= 9.60 and float(summary["stress_drawdown_percent"]) <= 10.00
        rows.append({"risk_multiplier": multiplier, **summary, "safe": safe, "new_candidates": int((active.priority_class == 1).sum())})
        outputs[multiplier] = (summary, trades, skipped, replay, active, admitted)
    return pd.DataFrame(rows), outputs


def stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v15.dollar_stats(frame)


def attribution(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {}
    return {str(key): stats(group) for key, group in frame.groupby(column, dropna=False)}


def selected_records(selected: pd.DataFrame) -> list[dict[str, Any]]:
    keys = ("sleeve_id", "symbol", "family", "profile", "timeframe", "risk_percent", "robust_score", "training", "validation", "audit", "development", "holdout_shadow")
    return [{key: item[key] for key in keys} for item in selected.to_dict("records")]


def report(payload: dict[str, Any]) -> None:
    baseline, fitted, forward = payload["baseline_v14_9"], payload["confirmation_fit_portfolio"], payload["forward_holdout_portfolio"]
    new = payload["forward_new_system_contribution"]
    lines = [
        "# V15.1 Currency-Factor and Session Portfolio", "",
        "**Data:** FXCM official weekly H1 bid/ask archive", "**Starting balance:** $5,000.00",
        "**Selection window:** 2016-2023", "**Untouched profile-selection holdout:** 2024-2026", "",
        "## Confirmation-fitted capacity view", "",
        "| Metric | V14.9 | V15.1 | Change |", "|---|---:|---:|---:|",
        f"| Net profit | ${baseline['net_profit']:,.2f} | ${fitted['net_profit']:,.2f} | ${fitted['net_profit']-baseline['net_profit']:,.2f} |",
        f"| Ending balance | ${baseline['ending_balance']:,.2f} | ${fitted['ending_balance']:,.2f} | ${fitted['ending_balance']-baseline['ending_balance']:,.2f} |",
        f"| Profit factor | {float(baseline['profit_factor'] or 0):.4f} | {float(fitted['profit_factor'] or 0):.4f} | {float(fitted['profit_factor'] or 0)-float(baseline['profit_factor'] or 0):.4f} |",
        f"| Max closed DD | {baseline['max_closed_drawdown_percent']:.4f}% | {fitted['max_closed_drawdown_percent']:.4f}% | {fitted['max_closed_drawdown_percent']-baseline['max_closed_drawdown_percent']:.4f} pp |",
        f"| Stress DD | {baseline['stress_drawdown_percent']:.4f}% | {fitted['stress_drawdown_percent']:.4f}% | {fitted['stress_drawdown_percent']-baseline['stress_drawdown_percent']:.4f} pp |", "",
        "The capacity view includes years used for profile selection and is not an untouched out-of-sample result.", "",
        "## Frozen 2024-2026 holdout", "",
        "| Metric | Combined | New systems only |", "|---|---:|---:|",
        f"| Net profit | ${forward['net_profit']:,.2f} | ${new['net_profit']:,.2f} |",
        f"| Profit factor | {float(forward['profit_factor'] or 0):.4f} | {float(new['profit_factor'] or 0):.4f} |",
        f"| Trades | {forward['closed_trades']} | {new['trades']} |",
        f"| Max closed DD | {forward['max_closed_drawdown_percent']:.4f}% | — |",
        f"| Stress DD | {forward['stress_drawdown_percent']:.4f}% | — |", "",
        "## Targets", "",
        f"- $20,000 fitted-capacity target reached: **{payload['target_20k_reached']}**; gap ${payload['target_20k_gap']:,.2f}.",
        f"- $40,000 stretch target reached: **{payload['target_40k_reached']}**; gap ${payload['target_40k_gap']:,.2f}.", "",
        "## Selected frozen profiles", "",
        "| Symbol | Family | Profile | Risk | Development net R | PF | Holdout net R |", "|---|---|---|---:|---:|---:|---:|",
    ]
    for item in payload["selected_profiles"]:
        lines.append(f"| {item['symbol']} | {item['family']} | {item['profile']} | {item['risk_percent']:.2f}% | {item['development']['net_r']:.2f} | {float(item['development']['profit_factor'] or 0):.3f} | {item['holdout_shadow']['net_r']:.2f} |")
    lines += ["", "Research only. V14.9 risk controls are unchanged and no live execution code was modified."]
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
    source = pd.concat([frame for frame in (old, fresh) if frame is not None and not frame.empty], ignore_index=True, sort=False)
    source["entry_time"] = pd.to_datetime(source.entry_time, utc=True)
    source["exit_time"] = pd.to_datetime(source.exit_time, utc=True)
    source = source[(source.entry_time >= START) & (source.entry_time <= END)]
    source["profile"] = source.profile.astype(str)
    source["family"] = source.family.astype(str)
    source["setup"] = "v15_1_" + source.symbol.astype(str).str.lower() + "_" + source.profile.str.lower()
    source["sleeve_id"] = source.symbol.astype(str) + "/" + source.family + "/" + source.profile
    source["priority_class"] = 1
    source.to_csv(OUT / "all_v15_1_candidates.csv", index=False)

    evidence, selected = build_evidence(source)
    selected_ids = set(selected.sleeve_id)
    flatten_evidence(evidence, selected_ids).to_csv(OUT / "profile_evidence.csv", index=False)
    (OUT / "selected_profiles.json").write_text(json.dumps(selected_records(selected), indent=2, default=str), encoding="utf-8")

    fitted_source = materialize(source, selected, FIT_START)
    forward_source = materialize(source, selected, HOLDOUT_START)
    fitted_source.to_csv(OUT / "selected_confirmation_candidates.csv", index=False)
    forward_source.to_csv(OUT / "selected_forward_candidates.csv", index=False)

    fitted_grid, fitted_outputs = run_grid(baseline, fitted_source)
    forward_grid, forward_outputs = run_grid(baseline, forward_source)
    fitted_grid.to_csv(OUT / "confirmation_risk_grid.csv", index=False)
    forward_grid.to_csv(OUT / "forward_risk_grid.csv", index=False)
    fitted_summary, fitted_trades, fitted_skipped, fitted_replay, fitted_active, fitted_admitted = fitted_outputs[1.0]
    forward_summary, forward_trades, forward_skipped, forward_replay, forward_active, forward_admitted = forward_outputs[1.0]

    for summary in (fitted_summary, forward_summary):
        if float(summary["max_closed_drawdown_percent"]) > 9.60 or float(summary["stress_drawdown_percent"]) > 10.00:
            raise RuntimeError(f"Primary V15.1 result unsafe: {summary}")

    fitted_admitted.to_csv(OUT / "confirmation_gate_and_admission.csv", index=False)
    forward_admitted.to_csv(OUT / "forward_gate_and_admission.csv", index=False)
    fitted_trades.to_csv(OUT / "confirmation_closed_trades.csv", index=False)
    forward_trades.to_csv(OUT / "forward_closed_trades.csv", index=False)
    fitted_skipped.to_csv(OUT / "confirmation_skipped_candidates.csv", index=False)
    forward_skipped.to_csv(OUT / "forward_skipped_candidates.csv", index=False)

    forward_new = forward_trades[forward_trades.get("priority_class", 0) == 1].copy()
    holdout = forward_trades[pd.to_datetime(forward_trades.entry_time, utc=True) >= HOLDOUT_START].copy()

    old_out, old_start, old_end = v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END
    v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = OUT, v15.TEST_START, END
    try:
        monthly, annual = v15.external.time_series(fitted_trades)
        monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
        annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
        v15.external.plot_outputs(monthly, annual, fitted_trades)
    finally:
        v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = old_out, old_start, old_end

    target20 = float(fitted_summary["net_profit"]) >= TARGET_20K
    target40 = float(fitted_summary["net_profit"]) >= TARGET_40K
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "provider": "FXCM official weekly H1 bid/ask archive",
        "selection_window": {"start": START.isoformat(), "end": (HOLDOUT_START-pd.Timedelta(seconds=1)).isoformat()},
        "holdout_window": {"start": HOLDOUT_START.isoformat(), "end": END.isoformat()},
        "selection_protocol": {"holdout_used_for_selection": False, "confirmation_fit_is_out_of_sample": False, "forward_holdout_is_out_of_sample_for_profile_selection": True, "one_profile_per_symbol_family": True},
        "risk_limits": {"maximum_new_trade_percent": v15.MAX_NEW_TRADE_RISK, "maximum_new_open_risk_percent": v15.MAX_NEW_OPEN_RISK, "maximum_new_positions": v15.MAX_NEW_POSITIONS, "maximum_ict_open_risk_percent": v15.MAX_ICT_OPEN_RISK, "maximum_combined_open_risk_percent": v15.MAX_COMBINED_OPEN_RISK, "maximum_closed_drawdown_percent": 9.60, "maximum_stressed_drawdown_percent": 10.0},
        "data_quality": quality, "baseline_sleeve_evidence": baseline_evidence, "selected_profiles": selected_records(selected),
        "baseline_v14_9": baseline_summary, "confirmation_fit_portfolio": {**fitted_summary, "safe": True}, "forward_holdout_portfolio": {**forward_summary, "safe": True},
        "forward_2024_2026_combined": stats(holdout), "forward_new_system_contribution": stats(forward_new),
        "confirmation_attribution_by_family": attribution(fitted_trades, "family"), "confirmation_attribution_by_symbol": attribution(fitted_trades, "symbol"),
        "forward_attribution_by_family": attribution(forward_trades, "family"), "forward_attribution_by_symbol": attribution(forward_trades, "symbol"),
        "confirmation_risk_grid": fitted_grid.to_dict("records"), "forward_risk_grid": forward_grid.to_dict("records"),
        "target_20k_reached": target20, "target_20k_gap": round(max(0.0, TARGET_20K-float(fitted_summary["net_profit"])), 2),
        "target_40k_reached": target40, "target_40k_gap": round(max(0.0, TARGET_40K-float(fitted_summary["net_profit"])), 2),
        "research_only": True, "live_execution_changed": False,
    }
    (OUT / "v15_1_currency_factor_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    report(payload)
    print(json.dumps({"baseline": baseline_summary, "selected": list(selected.sleeve_id), "confirmation_fit": fitted_summary, "forward_holdout": forward_summary, "forward_new": payload["forward_new_system_contribution"], "target_20k": target20, "target_20k_gap": payload["target_20k_gap"], "target_40k": target40, "target_40k_gap": payload["target_40k_gap"], "output": str(OUT)}, indent=2, default=str))


if __name__ == "__main__":
    main()
