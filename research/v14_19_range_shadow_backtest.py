"""V14.19 principal-parity replay plus range-engine shadow backtest."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import research.v14_18_hierarchical_regime_meta_backtest as parent
from mt5_ai_bridge.v14_19_range_mean_reversion_shadow import (
    CORE_SYMBOLS,
    apply_scenario_reserve,
    generate_shadow_trades,
    shadow_configuration,
)
from research.v14_14_extended_cost_backtest import EXTENDED_COST_SCENARIOS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_19_range_mean_reversion_shadow_output"
DATA = Path(
    os.getenv(
        "FXCM_OUT",
        str(ROOT / "research" / "fxcm_v14_19_range_2013_2026_data"),
    )
)
TEST_START = pd.Timestamp("2016-01-01T00:00:00Z")
TEST_END = pd.Timestamp("2026-05-31T23:59:59Z")
FORWARD_START = pd.Timestamp("2024-01-01T00:00:00Z")

V14_18_REFERENCE_NET = {
    "zero_cost": 34690.840749742056,
    "demo_cost": 14419.208668660005,
    "retail_cost": 13150.502997070864,
    "stress_cost": 10401.999888021403,
    "severe_cost": 7122.814704249973,
    "extreme_cost": 5409.31144906597,
}

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
        FORWARD_START,
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
    output: dict[str, Any] = {}
    work = frame.copy()
    work["entry_time"] = pd.to_datetime(work["entry_time"], utc=True)
    for name, (start, end) in BLOCKS.items():
        output[name] = ratio_stats(
            work[(work["entry_time"] >= start) & (work["entry_time"] <= end)]
        )
    return output


def symbol_stats(frame: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for symbol, group in frame.groupby("symbol"):
        output[str(symbol)] = ratio_stats(group)
    return output


def build_shadow_source() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in CORE_SYMBOLS:
        bid = load_h1(symbol, "bid")
        ask = load_h1(symbol, "ask")
        generated = generate_shadow_trades(symbol, bid, ask)
        if not generated.empty:
            frames.append(generated)
    if not frames:
        raise RuntimeError("V14.19 generated no range shadow trades")
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


def promotion_gate(scenario_results: dict[str, Any]) -> dict[str, Any]:
    retail = scenario_results["retail_cost"]["summary"]
    stress = scenario_results["stress_cost"]["summary"]
    forward_retail = scenario_results["retail_cost"]["blocks"][
        "forward_2024_2026"
    ]
    requirements = {
        "minimum_100_shadow_trades": retail["trades"] >= 100,
        "retail_positive": (
            retail["net_r"] > 0
            and float(retail["profit_factor"] or 0) > 1.0
        ),
        "stress_positive": (
            stress["net_r"] > 0
            and float(stress["profit_factor"] or 0) > 1.0
        ),
        "forward_2024_2026_positive": (
            forward_retail["net_r"] > 0
            and float(forward_retail["profit_factor"] or 0) > 1.0
        ),
        "chronological_blocks_nonnegative": all(
            scenario_results["retail_cost"]["blocks"][name]["net_r"] >= 0
            for name in (
                "development_2016_2018",
                "confirmation_2019_2020",
                "audit_2021_2022",
                "forward_2023_2026",
            )
        ),
    }
    return {
        "requirements": requirements,
        "evidence_gate_passed": all(requirements.values()),
        "live_promotion_authorized": False,
        "status": "SHADOW_ONLY_REGARDLESS_OF_BACKTEST",
    }


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.19 Range Mean-Reversion Shadow Engine",
        "",
        "V14.19 is stacked on the validated V14.18 hierarchical regime meta-labeler. The principal six-scenario portfolio is replayed unchanged. The new D1 range mean-reversion family is evaluated separately with official FXCM H1 bid/ask candles and has zero requested risk, zero executed risk and no broker transmission.",
        "",
        "## Principal V14.18 portfolio parity",
        "",
        "| Scenario | V14.18 reference | V14.19 principal | Difference |",
        "|---|---:|---:|---:|",
    ]
    for scenario, reference in V14_18_REFERENCE_NET.items():
        summary = payload["principal_results"][f"{scenario}/v14_13"]["summary"]
        actual = float(summary["net_profit"])
        lines.append(
            f"| {scenario} | ${reference:,.2f} | ${actual:,.2f} | "
            f"${actual-reference:,.2f} |"
        )

    lines += [
        "",
        "## Range shadow results",
        "",
        "| Scenario | Trades | Net R | Expectancy | PF | Win rate | Max DD (R) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario, item in payload["range_shadow"]["scenarios"].items():
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
        "## Frozen shadow design",
        "",
        "- Completed D1 data only; signals become available on the next UTC day.",
        "- Entry is the first later H4 bar at 08:00, 12:00 or 16:00 UTC.",
        "- Range state requires low ADX, limited EMA separation and bounded prior range width.",
        "- BUY/SELL candidates require an extreme 20-day z-score plus a partial close reclaim.",
        "- Stops use 2.20 D1 ATR, targets use 1.60R and the time exit is 15 H4 bars.",
        "- Bid/ask execution is modeled directly; a 0.025R reserve and scenario reserve are deducted.",
        "- Stop-first ordering is used when a bar contains both stop and target.",
        "- One shadow trade per symbol may be open at a time.",
        "- Every record has zero requested and executed risk and `transmitted=False`.",
        "",
        "## Promotion status",
        "",
        f"Evidence gate passed: **{payload['range_shadow']['promotion_gate']['evidence_gate_passed']}**.",
        "",
        "The engine remains shadow-only regardless of this historical result. Live or funded promotion is not authorized by V14.19.",
        "",
        "Historical modeled performance does not guarantee future results.",
    ]
    (OUT / "V14_19_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    parent.OUT = OUT
    parent.main()
    parent_path = OUT / "v14_18_results.json"
    parent_payload = json.loads(parent_path.read_text(encoding="utf-8"))

    source = build_shadow_source()
    source.to_csv(OUT / "range_shadow_source_trades.csv", index=False)

    scenario_results: dict[str, Any] = {}
    for scenario, costs in EXTENDED_COST_SCENARIOS.items():
        reserve = float(costs["V12"])
        ledger = apply_scenario_reserve(
            source,
            scenario=scenario,
            additional_cost_r=reserve,
        )
        ledger.to_csv(
            OUT / f"range_shadow_{scenario}_trades.csv",
            index=False,
        )
        scenario_results[scenario] = {
            "additional_cost_r": reserve,
            "summary": ratio_stats(ledger),
            "blocks": block_stats(ledger),
            "symbols": symbol_stats(ledger),
        }

    gate = promotion_gate(scenario_results)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "V14.19_RANGE_MEAN_REVERSION_SHADOW",
        "parent_model": "V14.18_HIERARCHICAL_REGIME_META_LABELER",
        "research_only": True,
        "shadow_only": True,
        "live_execution_changed": False,
        "principal_portfolio_changed": False,
        "principal_reference_net": V14_18_REFERENCE_NET,
        "principal_results": parent_payload["results"],
        "principal_dual_mode_coverage": parent_payload["dual_mode_coverage"],
        "range_shadow": {
            "data_provider": "FXCM official H1 bid/ask archive",
            "window": {
                "start": TEST_START.isoformat(),
                "end": TEST_END.isoformat(),
            },
            "configuration": shadow_configuration(),
            "source_trades": int(len(source)),
            "scenarios": scenario_results,
            "promotion_gate": gate,
        },
    }
    (OUT / "v14_19_results.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    shutil.copy2(
        parent_path,
        OUT / "v14_19_parent_v14_18_results.json",
    )
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
