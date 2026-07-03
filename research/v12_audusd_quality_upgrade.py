"""Exploratory AUDUSD quality-gate upgrade for the V12 five-symbol model.

The broad AUDUSD engine remains the baseline. This runner builds feature-rich
AUDUSD candidates, selects a lower-frequency quality rule using only the first
50% development segment plus a 20% confirmation segment, and reserves the final
30% as untouched validation. The selected rule is then replayed through the
same protected V12 portfolio gate at reduced risk.

Research only. It never connects to MT5 or sends orders.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

import v12_plus_validated_assets_backtest as study
import v12_plus_protected_assets_backtest as protected

OUT = study.ROOT / "research" / "v12_audusd_quality_output"
OUT.mkdir(parents=True, exist_ok=True)

WEEKDAY_RULES = {
    "all": (0, 1, 2, 3, 4),
    "mon_thu": (0, 1, 2, 3),
    "no_wednesday": (0, 1, 3, 4),
    "no_friday": (0, 1, 2, 3),
    "mon_wed_thu": (0, 2, 3),
    "mon_thu_only": (0, 3),
    "mon_thu_fri": (0, 3, 4),
    "mon_fri": (0, 4),
}


def feature_candidates(h4: pd.DataFrame, params: study.AUDUSDParams) -> pd.DataFrame:
    rows: list[dict] = []
    for i in range(60, len(h4) - 1):
        row = h4.iloc[i]
        signal_time = pd.Timestamp(row["end"])
        if int(signal_time.hour) not in params.allowed_hours:
            continue
        required = [
            row["atr14"], row["ema20"], row["ema50"], row["adx14"],
            row["dclose"], row["dema20"], row["dema50"], row["body_ratio"],
            row["volume_ratio"], row["atr_ratio"],
        ]
        if any(not np.isfinite(value) for value in required):
            continue
        atr_value = float(row["atr14"])
        long_regime = (
            row["dclose"] > row["dema20"] > row["dema50"]
            and row["ema20"] > row["ema50"]
        )
        short_regime = (
            row["dclose"] < row["dema20"] < row["dema50"]
            and row["ema20"] < row["ema50"]
        )
        side = 0
        if (
            long_regime
            and row["adx14"] >= params.adx_min
            and row["low"] <= row["ema20"] + params.touch_atr * atr_value
            and row["low"] >= row["ema50"] - 0.35 * atr_value
            and row["close"] > row["ema20"]
            and row["close"] > row["open"]
            and row["body_ratio"] >= params.body_ratio_min
        ):
            side = 1
        elif (
            short_regime
            and row["adx14"] >= params.adx_min
            and row["high"] >= row["ema20"] - params.touch_atr * atr_value
            and row["high"] <= row["ema50"] + 0.35 * atr_value
            and row["close"] < row["ema20"]
            and row["close"] < row["open"]
            and row["body_ratio"] >= params.body_ratio_min
        ):
            side = -1
        if not side:
            continue
        exit_time, result_r = study._simulate(
            h4,
            i,
            side,
            params.stop_atr,
            params.target_r,
            params.trail_atr,
            params.max_bars,
        )
        close_extension = side * (float(row["close"]) - float(row["ema20"])) / atr_value
        touch_depth = (
            (float(row["ema20"]) - float(row["low"])) / atr_value
            if side > 0
            else (float(row["high"]) - float(row["ema20"])) / atr_value
        )
        rows.append({
            **study._candidate(
                "AUDUSD",
                "AUDUSD_TREND_PULLBACK",
                "D1_H4_EMA_PULLBACK_QUALITY",
                side,
                signal_time,
                exit_time,
                params.risk_percent,
                result_r,
            ),
            "signal_hour": int(signal_time.hour),
            "signal_weekday": int(signal_time.weekday()),
            "adx14": float(row["adx14"]),
            "body_ratio": float(row["body_ratio"]),
            "volume_ratio": float(row["volume_ratio"]),
            "atr_ratio": float(row["atr_ratio"]),
            "ema_gap_atr": abs(float(row["ema20"]) - float(row["ema50"])) / atr_value,
            "close_extension_atr": float(close_extension),
            "touch_depth_atr": float(touch_depth),
        })
    return pd.DataFrame(rows)


def apply_rule(frame: pd.DataFrame, rule: dict) -> pd.DataFrame:
    mask = (
        frame["signal_hour"].isin(rule["hours"])
        & frame["signal_weekday"].isin(rule["weekdays"])
        & (frame["adx14"] >= rule["min_adx"])
        & (frame["body_ratio"] >= rule["min_body"])
        & (frame["volume_ratio"] >= rule["min_volume"])
        & (frame["atr_ratio"] >= rule["min_atr_ratio"])
        & (frame["ema_gap_atr"] <= rule["max_ema_gap"])
        & (frame["close_extension_atr"] <= rule["max_extension"])
    )
    return frame[mask].copy().reset_index(drop=True)


def _expectancy(result: dict) -> float:
    return float(result["net_r"]) / max(1, int(result["trades"]))


def select_quality_rule(frame: pd.DataFrame, h4: pd.DataFrame) -> tuple[dict, pd.DataFrame, dict]:
    start = h4["time"].min()
    span = h4["time"].max() - start
    development_end = start + span * 0.50
    confirmation_end = start + span * 0.70

    candidates = []
    hour_rules = ((4, 8), (8,), (4,))
    for hours, (weekday_name, weekdays), min_adx, min_body, min_volume, min_atr, max_gap, max_extension in itertools.product(
        hour_rules,
        WEEKDAY_RULES.items(),
        (15.0, 18.0, 20.0, 22.0),
        (0.25, 0.35, 0.45),
        (0.0, 0.80, 1.00, 1.20),
        (0.0, 0.80, 1.00, 1.10),
        (0.80, 1.20, 1.60, math.inf),
        (0.50, 0.80, 1.20, math.inf),
    ):
        rule = {
            "hours": tuple(hours),
            "weekday_name": weekday_name,
            "weekdays": tuple(weekdays),
            "min_adx": float(min_adx),
            "min_body": float(min_body),
            "min_volume": float(min_volume),
            "min_atr_ratio": float(min_atr),
            "max_ema_gap": float(max_gap),
            "max_extension": float(max_extension),
        }
        selected = apply_rule(frame, rule)
        development = study._stats(selected[selected["entry_time"] < development_end])
        confirmation = study._stats(
            selected[
                (selected["entry_time"] >= development_end)
                & (selected["entry_time"] < confirmation_end)
            ]
        )
        if development["trades"] < 30 or confirmation["trades"] < 10:
            continue
        if development["net_r"] <= 0 or confirmation["net_r"] <= 0:
            continue
        if development["profit_factor"] < 1.20 or confirmation["profit_factor"] < 1.05:
            continue
        dev_exp = _expectancy(development)
        confirm_exp = _expectancy(confirmation)
        stability = min(dev_exp, confirm_exp)
        score = (
            stability * math.sqrt(development["trades"] + confirmation["trades"])
            + 0.15 * min(development["profit_factor"], confirmation["profit_factor"])
            - abs(dev_exp - confirm_exp)
        )
        candidates.append((score, rule, development, confirmation, selected))

    if not candidates:
        raise RuntimeError("No AUDUSD quality rule passed development and confirmation gates")
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, rule, development, confirmation, selected = candidates[0]
    holdout = study._stats(selected[selected["entry_time"] >= confirmation_end])
    passed = (
        holdout["trades"] >= 15
        and holdout["net_r"] > 0
        and holdout["profit_factor"] >= 1.05
    )
    report = {
        "development_end": development_end.isoformat(),
        "confirmation_end": confirmation_end.isoformat(),
        "score": float(score),
        "rule": rule,
        "development": development,
        "confirmation": confirmation,
        "untouched_holdout": holdout,
        "passed": bool(passed),
        "top_rules": [
            {
                "score": float(item[0]),
                "rule": item[1],
                "development": item[2],
                "confirmation": item[3],
            }
            for item in candidates[:10]
        ],
    }
    return rule, selected, report


def merge_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    usable = [frame for frame in frames if not frame.empty]
    return pd.concat(usable, ignore_index=True).sort_values(
        ["entry_time", "engine", "setup"]
    ).reset_index(drop=True)


def build_legacy(prepared: dict) -> pd.DataFrame:
    frames = [study._gbpusd_precision(prepared["GBPUSD"][1])]
    for symbol in study.LEGACY_SYMBOLS:
        h1, h4, _ = prepared[symbol]
        frames.append(study._v12_core_candidates(symbol, h4))
        if symbol == "GBPUSD":
            frames.append(study._gbpusd_retest_candidates(h4))
        else:
            frames.append(study._h1_retest_candidates(symbol, h1, h4))
    return merge_frames(*frames)


def main() -> None:
    study._guard_decision = protected.protected_guard_decision
    prepared = {symbol: study._prepare(symbol) for symbol in study.ALL_SYMBOLS}
    legacy = build_legacy(prepared)

    broad_params, broad_audusd, broad_report = study._select_audusd(prepared["AUDUSD"][1])
    rich_audusd = feature_candidates(prepared["AUDUSD"][1], broad_params)
    rule, quality_audusd, quality_report = select_quality_rule(
        rich_audusd, prepared["AUDUSD"][1]
    )
    if not quality_report["passed"]:
        quality_audusd = quality_audusd.iloc[0:0].copy()
    quality_audusd["risk_percent"] = 0.15
    quality_audusd["setup"] = "AUDUSD_QUALITY_PULLBACK_REDUCED_RISK"

    usdjpy = study._usdjpy_candidates(prepared["USDJPY"][1])
    usdjpy_report = study._validation_report(usdjpy, prepared["USDJPY"][1])
    if not usdjpy_report["passed"]:
        usdjpy = usdjpy.iloc[0:0].copy()

    common_end = min(prepared[symbol][1]["time"].max() for symbol in study.ALL_SYMBOLS)
    common_start = max(prepared[symbol][1]["time"].min() for symbol in study.ALL_SYMBOLS)
    windows = {
        "max": common_start,
        "5y": max(common_start, common_end - pd.DateOffset(years=5)),
        "3y": max(common_start, common_end - pd.DateOffset(years=3)),
        "2y": max(common_start, common_end - pd.DateOffset(years=2)),
        "1y": max(common_start, common_end - pd.DateOffset(years=1)),
        "6m": max(common_start, common_end - pd.DateOffset(months=6)),
    }
    scenarios = {
        "broad_audusd_baseline": merge_frames(legacy, broad_audusd, usdjpy),
        "quality_audusd_upgrade": merge_frames(legacy, quality_audusd, usdjpy),
        "no_audusd_control": merge_frames(legacy, usdjpy),
    }

    results = {
        "status": "EXPLORATORY_RESEARCH_ONLY_DO_NOT_DEPLOY",
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "starting_balance": study.STARTING_BALANCE,
        "broad_audusd_report": broad_report,
        "quality_rule": rule,
        "quality_report": quality_report,
        "quality_risk_percent": 0.15,
        "usdjpy_report": usdjpy_report,
        "scenarios": {},
    }
    summary_rows = []
    symbol_rows = []
    engine_rows = []
    for scenario, candidates in scenarios.items():
        results["scenarios"][scenario] = {}
        candidates.to_csv(OUT / f"{scenario}_candidates.csv", index=False)
        for window, start in windows.items():
            summary, accepted, rejected = study._replay(
                candidates, start, common_end, study.CAPACITY_CAPS
            )
            results["scenarios"][scenario][window] = summary
            summary_rows.append({"scenario": scenario, "window": window, **summary})
            symbol_rows.append(study._attribution(accepted, scenario, window, "symbol"))
            engine_rows.append(study._attribution(accepted, scenario, window, "engine"))
            accepted.to_csv(OUT / f"accepted_{scenario}_{window}.csv", index=False)
            rejected.to_csv(OUT / f"rejected_{scenario}_{window}.csv", index=False)

    summary = pd.DataFrame(summary_rows)
    by_symbol = pd.concat(symbol_rows, ignore_index=True)
    by_engine = pd.concat(engine_rows, ignore_index=True)
    summary.to_csv(OUT / "scenario_summary.csv", index=False)
    by_symbol.to_csv(OUT / "profit_by_symbol.csv", index=False)
    by_engine.to_csv(OUT / "profit_by_engine.csv", index=False)
    rich_audusd.to_csv(OUT / "audusd_feature_candidates.csv", index=False)
    quality_audusd.to_csv(OUT / "audusd_quality_candidates.csv", index=False)
    (OUT / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        "# V12 AUDUSD Quality Upgrade",
        "",
        "Status: **EXPLORATORY RESEARCH ONLY — DO NOT DEPLOY**",
        "",
        f"Selected rule: `{json.dumps(rule, sort_keys=True)}`",
        "",
        "## Quality validation",
        "",
        f"- Development: {quality_report['development']}",
        f"- Confirmation: {quality_report['confirmation']}",
        f"- Untouched holdout: {quality_report['untouched_holdout']}",
        f"- Passed: **{quality_report['passed']}**",
        "",
        "## Portfolio comparison",
        "",
        "| Scenario | Window | Trades | Net profit | Ending balance | PF | Max DD | Stress DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.scenario} | {row.window} | {row.trades} | ${row.net_profit:.2f} | "
            f"${row.ending_balance:.2f} | {row.profit_factor:.3f} | "
            f"{row.max_drawdown_percent:.2f}% | {row.stress_drawdown_percent:.2f}% |"
        )
    (OUT / "V12_AUDUSD_QUALITY_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
