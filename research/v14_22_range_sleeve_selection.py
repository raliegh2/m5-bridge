"""Development-only symbol/profile sleeve selection for V14.22.

This script consumes the pre-registered profile ledgers produced by
``v14_22_range_breakout_retest_backtest.py``. It selects at most one profile
per symbol using retail-cost data from 2016-2020 only, freezes those sleeves,
and then reports untouched 2021-2026 audit/forward results.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v14_19_range_mean_reversion_shadow import CORE_SYMBOLS
from mt5_ai_bridge.v14_22_range_breakout_retest_shadow import PROFILES
from research.v14_14_extended_cost_backtest import EXTENDED_COST_SCENARIOS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_22_range_breakout_retest_output"
DEVELOPMENT_END = pd.Timestamp("2020-12-31T23:59:59Z")
TEST_END = pd.Timestamp("2026-05-31T23:59:59Z")
BLOCKS = {
    "development_2016_2018": (
        pd.Timestamp("2016-01-01T00:00:00Z"),
        pd.Timestamp("2018-12-31T23:59:59Z"),
    ),
    "confirmation_2019_2020": (
        pd.Timestamp("2019-01-01T00:00:00Z"),
        pd.Timestamp("2020-12-31T23:59:59Z"),
    ),
    "audit_2021_2022": (
        pd.Timestamp("2021-01-01T00:00:00Z"),
        pd.Timestamp("2022-12-31T23:59:59Z"),
    ),
    "forward_2023_2026": (
        pd.Timestamp("2023-01-01T00:00:00Z"),
        TEST_END,
    ),
    "forward_2024_2026": (
        pd.Timestamp("2024-01-01T00:00:00Z"),
        TEST_END,
    ),
}


def ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    values = pd.to_numeric(
        frame.get("r_multiple", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    if values.empty:
        return {
            "trades": 0,
            "net_r": 0.0,
            "expectancy_r": None,
            "profit_factor": None,
            "win_rate": None,
            "maximum_drawdown_r": 0.0,
        }
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    equity = values.cumsum()
    drawdown = equity.cummax().clip(lower=0.0) - equity
    return {
        "trades": int(len(values)),
        "net_r": float(values.sum()),
        "expectancy_r": float(values.mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 99.0,
        "win_rate": float((values > 0).mean()),
        "maximum_drawdown_r": float(drawdown.max()),
    }


def slice_frame(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    entries = pd.to_datetime(frame["entry_time"], utc=True)
    return frame[(entries >= start) & (entries <= end)]


def block_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        name: ratio_stats(slice_frame(frame, start, end))
        for name, (start, end) in BLOCKS.items()
    }


def symbol_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        str(symbol): ratio_stats(group)
        for symbol, group in frame.groupby("symbol")
    }


def load_profile_sources() -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    for profile in PROFILES:
        path = OUT / f"{profile.name.lower()}_source_trades.csv"
        frame = pd.read_csv(path)
        if not frame.empty:
            frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
            frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
        output[profile.name] = frame
    return output


def retail_ledger(source: pd.DataFrame) -> pd.DataFrame:
    output = source.copy()
    output["scenario"] = "retail_cost"
    output["scenario_additional_cost_r"] = float(
        EXTENDED_COST_SCENARIOS["retail_cost"]["V12"]
    )
    output["r_multiple"] = (
        pd.to_numeric(output["base_net_r_multiple"], errors="coerce")
        - output["scenario_additional_cost_r"]
    )
    return output


def development_record(ledger: pd.DataFrame, symbol: str) -> dict[str, Any]:
    symbol_frame = ledger[ledger["symbol"].eq(symbol)].copy()
    development = symbol_frame[
        pd.to_datetime(symbol_frame["entry_time"], utc=True) <= DEVELOPMENT_END
    ]
    total = ratio_stats(development)
    first = ratio_stats(slice_frame(development, *BLOCKS["development_2016_2018"]))
    second = ratio_stats(slice_frame(development, *BLOCKS["confirmation_2019_2020"]))
    requirements = {
        "minimum_20_development_trades": total["trades"] >= 20,
        "development_net_positive": total["net_r"] > 0,
        "development_pf_above_1_10": float(total["profit_factor"] or 0) > 1.10,
        "development_2016_2018_not_worse_than_minus_0_10r": first["net_r"] >= -0.10,
        "confirmation_2019_2020_not_worse_than_minus_0_10r": second["net_r"] >= -0.10,
    }
    score = (
        100.0 * min(float(first["expectancy_r"] or 0), float(second["expectancy_r"] or 0))
        + float(total["expectancy_r"] or 0)
        + 0.001 * total["trades"]
    )
    return {
        "summary": total,
        "development_2016_2018": first,
        "confirmation_2019_2020": second,
        "requirements": requirements,
        "eligible": all(requirements.values()),
        "score": float(score),
    }


def select_sleeves(sources: dict[str, pd.DataFrame]) -> dict[str, Any]:
    candidates: dict[str, Any] = {}
    selected: list[dict[str, Any]] = []
    for symbol in CORE_SYMBOLS:
        choices: list[tuple[float, str]] = []
        per_profile: dict[str, Any] = {}
        for profile in PROFILES:
            record = development_record(retail_ledger(sources[profile.name]), symbol)
            per_profile[profile.name] = record
            if record["eligible"]:
                choices.append((record["score"], profile.name))
        choices.sort(reverse=True)
        candidates[symbol] = per_profile
        if choices:
            score, profile_name = choices[0]
            selected.append(
                {
                    "symbol": symbol,
                    "profile": profile_name,
                    "development_score": float(score),
                }
            )
    return {
        "selection_window": "2016-01-01 through 2020-12-31 only",
        "method": "one highest-scoring eligible profile per symbol",
        "eligibility": {
            "minimum_development_trades": 20,
            "minimum_development_profit_factor": 1.10,
            "minimum_each_development_block_net_r": -0.10,
        },
        "candidates": candidates,
        "selected_sleeves": selected,
    }


def build_selected_source(
    sources: dict[str, pd.DataFrame],
    selected_sleeves: list[dict[str, Any]],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for sleeve in selected_sleeves:
        source = sources[str(sleeve["profile"])]
        frames.append(source[source["symbol"].eq(str(sleeve["symbol"]))].copy())
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True, sort=False)
        .sort_values(["entry_time", "symbol", "profile"])
        .reset_index(drop=True)
    )


def scenario_results(source: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for scenario, costs in EXTENDED_COST_SCENARIOS.items():
        reserve = float(costs["V12"])
        ledger = source.copy()
        ledger["scenario"] = scenario
        ledger["scenario_additional_cost_r"] = reserve
        ledger["r_multiple"] = (
            pd.to_numeric(ledger["base_net_r_multiple"], errors="coerce") - reserve
        )
        ledger.to_csv(OUT / f"selected_arm_{scenario}_trades.csv", index=False)
        development = ledger[
            pd.to_datetime(ledger["entry_time"], utc=True) <= DEVELOPMENT_END
        ]
        output[scenario] = {
            "additional_cost_r": reserve,
            "summary": ratio_stats(ledger),
            "development_total": ratio_stats(development),
            "blocks": block_stats(ledger),
            "symbols": symbol_stats(ledger),
        }
    return output


def promotion_gate(arm: dict[str, Any] | None) -> dict[str, Any]:
    if arm is None:
        return {
            "requirements": {"development_sleeves_selected": False},
            "evidence_gate_passed": False,
            "demo_promotion_authorized": False,
            "status": "NO_PROFITABLE_DEVELOPMENT_SLEEVES",
        }
    retail = arm["scenarios"]["retail_cost"]
    stress = arm["scenarios"]["stress_cost"]
    blocks = retail["blocks"]
    symbols = retail["symbols"]
    requirements = {
        "development_sleeves_selected": True,
        "minimum_80_total_trades": retail["summary"]["trades"] >= 80,
        "development_total_positive": (
            retail["development_total"]["net_r"] > 0
            and float(retail["development_total"]["profit_factor"] or 0) > 1.10
        ),
        "retail_total_positive": (
            retail["summary"]["net_r"] > 0
            and float(retail["summary"]["profit_factor"] or 0) > 1.10
        ),
        "stress_total_positive": (
            stress["summary"]["net_r"] > 0
            and float(stress["summary"]["profit_factor"] or 0) > 1.00
        ),
        "audit_2021_2022_positive": (
            blocks["audit_2021_2022"]["net_r"] > 0
            and float(blocks["audit_2021_2022"]["profit_factor"] or 0) > 1.0
        ),
        "forward_2023_2026_positive": (
            blocks["forward_2023_2026"]["net_r"] > 0
            and float(blocks["forward_2023_2026"]["profit_factor"] or 0) > 1.0
        ),
        "forward_2024_2026_positive": (
            blocks["forward_2024_2026"]["net_r"] > 0
            and float(blocks["forward_2024_2026"]["profit_factor"] or 0) > 1.0
        ),
        "every_selected_symbol_nonnegative": bool(symbols) and all(
            stats["net_r"] >= 0 for stats in symbols.values()
        ),
    }
    passed = all(requirements.values())
    return {
        "requirements": requirements,
        "evidence_gate_passed": passed,
        "demo_promotion_authorized": False,
        "status": (
            "EVIDENCE_PASSED_REQUIRES_SEPARATE_DEMO_INTEGRATION"
            if passed
            else "SHADOW_ONLY_EVIDENCE_FAILED"
        ),
    }


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.22 Development-Qualified Range Sleeves",
        "",
        "Profile and symbol admission was determined from retail-cost data through 2020 only. Later observations were not used to choose the sleeves.",
        "",
        "## Selected sleeves",
        "",
        "| Symbol | Profile | Development score |",
        "|---|---|---:|",
    ]
    sleeves = payload["selection"]["selected_sleeves"]
    if sleeves:
        for sleeve in sleeves:
            lines.append(
                f"| {sleeve['symbol']} | {sleeve['profile']} | {sleeve['development_score']:.4f} |"
            )
    else:
        lines.append("| NONE | NONE | 0.0000 |")
    arm = payload["selected_arm"]
    if arm:
        lines += [
            "",
            "## Frozen selected-arm results",
            "",
            "| Scenario | Trades | Net R | Expectancy | PF | Win rate | Max DD R |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for scenario, item in arm["scenarios"].items():
            stats = item["summary"]
            lines.append(
                f"| {scenario} | {stats['trades']} | {stats['net_r']:.4f} | "
                f"{float(stats['expectancy_r'] or 0):.4f} | "
                f"{float(stats['profit_factor'] or 0):.4f} | "
                f"{100.0*float(stats['win_rate'] or 0):.2f}% | "
                f"{stats['maximum_drawdown_r']:.4f} |"
            )
        lines += [
            "",
            "### Retail chronological blocks",
            "",
            "| Block | Trades | Net R | PF |",
            "|---|---:|---:|---:|",
        ]
        for name, stats in arm["scenarios"]["retail_cost"]["blocks"].items():
            lines.append(
                f"| {name} | {stats['trades']} | {stats['net_r']:.4f} | "
                f"{float(stats['profit_factor'] or 0):.4f} |"
            )
        lines += [
            "",
            "### Retail by selected symbol",
            "",
            "| Symbol | Trades | Net R | PF |",
            "|---|---:|---:|---:|",
        ]
        for symbol, stats in arm["scenarios"]["retail_cost"]["symbols"].items():
            lines.append(
                f"| {symbol} | {stats['trades']} | {stats['net_r']:.4f} | "
                f"{float(stats['profit_factor'] or 0):.4f} |"
            )
    gate = payload["promotion_gate"]
    lines += [
        "",
        "## Evidence status",
        "",
        f"Evidence gate passed: **{gate['evidence_gate_passed']}**.",
        "",
        f"Status: **{gate['status']}**.",
        "",
        "This remains research-only. No order transmission or risk allocation is included.",
    ]
    (OUT / "V14_22_SLEEVE_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    sources = load_profile_sources()
    selection = select_sleeves(sources)
    selected_source = build_selected_source(sources, selection["selected_sleeves"])
    selected_source.to_csv(OUT / "selected_arm_source_trades.csv", index=False)
    arm = None
    if not selected_source.empty:
        arm = {
            "source_trades": int(len(selected_source)),
            "sleeves": selection["selected_sleeves"],
            "scenarios": scenario_results(selected_source),
        }
    gate = promotion_gate(arm)
    payload = {
        "model": "V14.22_RANGE_BREAKOUT_RETEST_SLEEVES",
        "selection": selection,
        "selected_arm": arm,
        "promotion_gate": gate,
        "research_only": True,
        "shadow_only": True,
        "live_execution_changed": False,
    }
    (OUT / "v14_22_sleeve_results.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
