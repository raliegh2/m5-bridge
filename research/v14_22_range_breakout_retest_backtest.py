"""Chronological V14.22 range-breakout/retest profile selection and audit."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v14_19_range_mean_reversion_shadow import CORE_SYMBOLS
from mt5_ai_bridge.v14_22_range_breakout_retest_shadow import (
    PROFILES,
    RangeBreakoutRetestProfile,
    apply_scenario_reserve,
    generate_profile_trades,
    profile_configuration,
)
from research.v14_14_extended_cost_backtest import EXTENDED_COST_SCENARIOS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_22_range_breakout_retest_output"
DATA = Path(
    os.getenv(
        "FXCM_OUT",
        str(ROOT / "research" / "fxcm_v14_19_range_2013_2026_data"),
    )
)
TEST_START = pd.Timestamp("2016-01-01T00:00:00Z")
TEST_END = pd.Timestamp("2026-05-31T23:59:59Z")
DEVELOPMENT_END = pd.Timestamp("2020-12-31T23:59:59Z")

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


def load_h1(symbol: str, side: str) -> pd.DataFrame:
    path = DATA / f"{symbol}_H1_{side}.csv"
    frame = pd.read_csv(path)
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "tick_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["time", "open", "high", "low", "close"])
        .loc[
            lambda value: (
                (value["time"] >= TEST_START - pd.DateOffset(years=3))
                & (value["time"] <= TEST_END)
            )
        ]
        .sort_values("time")
        .drop_duplicates("time")
        .reset_index(drop=True)
    )


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
    peak = equity.cummax().clip(lower=0.0)
    drawdown = peak - equity
    return {
        "trades": int(len(values)),
        "net_r": float(values.sum()),
        "expectancy_r": float(values.mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 99.0,
        "win_rate": float((values > 0).mean()),
        "maximum_drawdown_r": float(drawdown.max()),
    }


def block_stats(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame.copy()
    work["entry_time"] = pd.to_datetime(work["entry_time"], utc=True)
    return {
        name: ratio_stats(work[(work["entry_time"] >= start) & (work["entry_time"] <= end)])
        for name, (start, end) in BLOCKS.items()
    }


def symbol_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        str(symbol): ratio_stats(group)
        for symbol, group in frame.groupby("symbol")
    }


def build_profile_source(profile: RangeBreakoutRetestProfile) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in CORE_SYMBOLS:
        bid = load_h1(symbol, "bid")
        ask = load_h1(symbol, "ask")
        generated = generate_profile_trades(symbol, bid, ask, profile)
        if not generated.empty:
            frames.append(generated)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    return (
        output[
            (output["entry_time"] >= TEST_START)
            & (output["entry_time"] <= TEST_END)
        ]
        .sort_values(["entry_time", "symbol"])
        .reset_index(drop=True)
    )


def development_selection(profile_results: dict[str, Any]) -> dict[str, Any]:
    """Select only from 2016-2020 retail results; later data is untouched."""
    eligible: list[tuple[float, str]] = []
    reasons: dict[str, dict[str, bool]] = {}
    for profile in PROFILES:
        item = profile_results[profile.name]["scenarios"]["retail_cost"]
        total = item["development_total"]
        first = item["blocks"]["development_2016_2018"]
        second = item["blocks"]["confirmation_2019_2020"]
        requirements = {
            "minimum_40_development_trades": total["trades"] >= 40,
            "development_net_positive": total["net_r"] > 0,
            "development_pf_above_1_05": float(total["profit_factor"] or 0) > 1.05,
            "development_2016_2018_nonnegative": first["net_r"] >= 0,
            "confirmation_2019_2020_nonnegative": second["net_r"] >= 0,
        }
        reasons[profile.name] = requirements
        if all(requirements.values()):
            minimum_block_expectancy = min(
                float(first["expectancy_r"] or 0),
                float(second["expectancy_r"] or 0),
            )
            score = (
                100.0 * minimum_block_expectancy
                + float(total["expectancy_r"] or 0)
                + 0.001 * total["trades"]
            )
            eligible.append((score, profile.name))
    eligible.sort(reverse=True)
    return {
        "selection_window": "2016-01-01 through 2020-12-31 only",
        "requirements_by_profile": reasons,
        "eligible_profiles": [name for _, name in eligible],
        "selected_profile": eligible[0][1] if eligible else None,
    }


def promotion_gate(selected: dict[str, Any] | None) -> dict[str, Any]:
    if selected is None:
        return {
            "requirements": {"development_profile_selected": False},
            "evidence_gate_passed": False,
            "demo_promotion_authorized": False,
            "status": "NO_PROFITABLE_DEVELOPMENT_PROFILE",
        }
    retail = selected["scenarios"]["retail_cost"]
    stress = selected["scenarios"]["stress_cost"]
    blocks = retail["blocks"]
    symbols = retail["symbols"]
    requirements = {
        "development_profile_selected": True,
        "minimum_100_total_trades": retail["summary"]["trades"] >= 100,
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
        "at_least_four_symbols_nonnegative": sum(
            stats["net_r"] >= 0 for stats in symbols.values()
        ) >= 4,
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
        "# V14.22 Range Breakout-Retest Research Arm",
        "",
        "V14.22 does not reactivate the failed V14.19 mean-reversion orders. It tests three pre-registered range-compression to H4 breakout/retest profiles with official FXCM H1 bid/ask data. Profile selection uses retail-cost trades from 2016-2020 only; 2021-2026 remains audit/forward evidence.",
        "",
        "## Development-only profile comparison",
        "",
        "| Profile | Development trades | Retail net R | PF | 2016-18 net R | 2019-20 net R |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for profile in PROFILES:
        retail = payload["profiles"][profile.name]["scenarios"]["retail_cost"]
        total = retail["development_total"]
        blocks = retail["blocks"]
        lines.append(
            f"| {profile.name} | {total['trades']} | {total['net_r']:.4f} | "
            f"{float(total['profit_factor'] or 0):.4f} | "
            f"{blocks['development_2016_2018']['net_r']:.4f} | "
            f"{blocks['confirmation_2019_2020']['net_r']:.4f} |"
        )
    selected = payload["selection"]["selected_profile"]
    lines += [
        "",
        f"Selected profile using development data only: **{selected or 'NONE'}**.",
        "",
    ]
    if selected:
        lines += [
            "## Frozen selected-profile results",
            "",
            "| Scenario | Trades | Net R | Expectancy | PF | Win rate | Max DD R |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for scenario, item in payload["selected_profile"]["scenarios"].items():
            stats = item["summary"]
            lines.append(
                f"| {scenario} | {stats['trades']} | {stats['net_r']:.4f} | "
                f"{float(stats['expectancy_r'] or 0):.4f} | "
                f"{float(stats['profit_factor'] or 0):.4f} | "
                f"{100.0*float(stats['win_rate'] or 0):.2f}% | "
                f"{stats['maximum_drawdown_r']:.4f} |"
            )
        retail_blocks = payload["selected_profile"]["scenarios"]["retail_cost"]["blocks"]
        lines += [
            "",
            "### Retail chronological blocks",
            "",
            "| Block | Trades | Net R | PF |",
            "|---|---:|---:|---:|",
        ]
        for name, stats in retail_blocks.items():
            lines.append(
                f"| {name} | {stats['trades']} | {stats['net_r']:.4f} | "
                f"{float(stats['profit_factor'] or 0):.4f} |"
            )
    gate = payload["promotion_gate"]
    lines += [
        "",
        "## Promotion status",
        "",
        f"Evidence gate passed: **{gate['evidence_gate_passed']}**.",
        "",
        f"Status: **{gate['status']}**.",
        "",
        "No V14.22 order transmission is included. Even a passing result requires a separate demo-forward integration and explicit safety review.",
        "",
        "Historical modeled performance does not guarantee future results.",
    ]
    (OUT / "V14_22_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    profile_results: dict[str, Any] = {}
    for profile in PROFILES:
        source = build_profile_source(profile)
        source.to_csv(OUT / f"{profile.name.lower()}_source_trades.csv", index=False)
        scenarios: dict[str, Any] = {}
        for scenario, costs in EXTENDED_COST_SCENARIOS.items():
            reserve = float(costs["V12"])
            ledger = apply_scenario_reserve(
                source,
                scenario=scenario,
                additional_cost_r=reserve,
            )
            ledger.to_csv(
                OUT / f"{profile.name.lower()}_{scenario}_trades.csv",
                index=False,
            )
            entries = pd.to_datetime(ledger.get("entry_time"), utc=True)
            development = ledger[entries <= DEVELOPMENT_END] if not ledger.empty else ledger
            scenarios[scenario] = {
                "additional_cost_r": reserve,
                "summary": ratio_stats(ledger),
                "development_total": ratio_stats(development),
                "blocks": block_stats(ledger),
                "symbols": symbol_stats(ledger),
            }
        profile_results[profile.name] = {
            "configuration": profile_configuration(profile),
            "source_trades": int(len(source)),
            "scenarios": scenarios,
        }

    selection = development_selection(profile_results)
    selected_name = selection["selected_profile"]
    selected = profile_results.get(selected_name) if selected_name else None
    gate = promotion_gate(selected)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "V14.22_RANGE_BREAKOUT_RETEST_RESEARCH",
        "parent_model": "V14.21_DEMO_AUTO_RUNNER",
        "research_only": True,
        "shadow_only": True,
        "live_execution_changed": False,
        "direct_v14_19_range_orders_enabled": False,
        "data_provider": "FXCM official H1 bid/ask archive",
        "window": {"start": TEST_START.isoformat(), "end": TEST_END.isoformat()},
        "profiles": profile_results,
        "selection": selection,
        "selected_profile": selected,
        "promotion_gate": gate,
    }
    (OUT / "v14_22_results.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
