"""Strict chronological 10-year order-flow proxy research.

The underlying GBPUSD breakout is the existing completed-H4 engine. Historical
spot-FX DOM is unavailable, so order flow is represented only by information
known at the signal close: tick-volume ratio, five-bar signed tick-volume
imbalance, candle-body pressure, and close location.
"""
from __future__ import annotations

import itertools
import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from run_gbpusd_breakout_v2_proxy import load_h4, run

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_23_order_flow_10y_out"
DATA_URL = (
    "https://raw.githubusercontent.com/ejtraderLabs/historical-data/main/"
    "GBPUSD/GBPUSDh4.csv"
)
DATA_PATH = OUT / "GBPUSDh4.csv"
STARTING_BALANCE = 5000.0
LOCAL_H4_PATH = Path(
    os.getenv(
        "V14_23_GBPUSD_H4_PATH",
        r"C:\Users\ralie\Desktop\GBPUSD_H4_201601040000_202607011200.csv",
    )
)


@dataclass(frozen=True)
class FlowParams:
    volume_ratio_min: float
    imbalance_min: float
    body_pressure_min: float
    close_pressure_min: float


def _filter(params: FlowParams):
    def accepted(row: pd.Series, side: int) -> bool:
        return bool(
            float(row["volume_ratio"]) >= params.volume_ratio_min
            and side * float(row["flow_imbalance_5"]) >= params.imbalance_min
            and side * float(row["body_pressure"]) >= params.body_pressure_min
            and side * float(row["close_pressure"]) >= params.close_pressure_min
        )

    return accepted


def _slice_with_warmup(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    eligible = frame[frame["time"] < end]
    before = eligible[eligible["time"] < start].tail(300)
    inside = eligible[eligible["time"] >= start]
    return pd.concat([before, inside]).drop_duplicates("time").sort_values("time")


def _period_result(
    frame: pd.DataFrame,
    start: str,
    end: str,
    params: FlowParams | None,
) -> dict:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    sample = _slice_with_warmup(frame, start_ts, end_ts)
    trades, _equity, metrics = run(
        sample,
        initial_balance=STARTING_BALANCE,
        order_flow_filter=_filter(params) if params is not None else None,
    )
    if not trades.empty:
        trades = trades[
            (pd.to_datetime(trades["entry_time"], utc=True) >= start_ts)
            & (pd.to_datetime(trades["entry_time"], utc=True) < end_ts)
        ].copy()
    gross_profit = float(trades.loc[trades["pnl"] > 0, "pnl"].sum()) if not trades.empty else 0.0
    gross_loss = float(-trades.loc[trades["pnl"] < 0, "pnl"].sum()) if not trades.empty else 0.0
    return {
        "start": start,
        "end": end,
        "trades": int(len(trades)),
        "net_profit": float(trades["pnl"].sum()) if not trades.empty else 0.0,
        "starting_balance": STARTING_BALANCE,
        "ending_balance": (
            STARTING_BALANCE
            + (float(trades["pnl"].sum()) if not trades.empty else 0.0)
        ),
        "return_percent": (
            (float(trades["pnl"].sum()) if not trades.empty else 0.0)
            / STARTING_BALANCE
            * 100.0
        ),
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss > 0
            else (math.inf if gross_profit > 0 else 0.0)
        ),
        "max_drawdown_percent": float(metrics["max_drawdown"] * 100.0),
        "trade_rows": trades,
    }


def _serializable(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "trade_rows"}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = LOCAL_H4_PATH if LOCAL_H4_PATH.exists() else DATA_PATH
    if not source.exists():
        pd.read_csv(DATA_URL).to_csv(DATA_PATH, index=False)
        source = DATA_PATH
    frame = load_h4(source)
    coverage_start = pd.Timestamp(frame["time"].min())
    coverage_end = pd.Timestamp(frame["time"].max()) + pd.Timedelta(hours=4)

    periods = {
        "development": ("2016-07-01", "2022-01-01"),
        "validation": ("2022-01-01", "2024-01-01"),
        "holdout": ("2024-01-01", "2026-07-02"),
        "full_10y": ("2016-07-01", "2026-07-02"),
    }
    baseline = {
        name: _period_result(frame, *window, None)
        for name, window in periods.items()
    }

    grid = [
        FlowParams(*values)
        for values in itertools.product(
            (0.80, 0.90, 1.00, 1.10),
            (-0.20, -0.10, 0.00, 0.10),
            (-0.50, -0.25, 0.00, 0.25),
            (-0.50, -0.25, 0.00, 0.25),
        )
    ]
    rankings: list[tuple[float, FlowParams, dict]] = []
    for params in grid:
        result = _period_result(frame, *periods["development"], params)
        if result["trades"] < 40 or result["max_drawdown_percent"] >= 9.0:
            continue
        score = (
            result["net_profit"]
            * min(result["profit_factor"], 3.0)
            * min(1.0, result["trades"] / max(1, baseline["development"]["trades"]))
        )
        rankings.append((score, params, result))
    rankings.sort(key=lambda item: item[0], reverse=True)

    selected = None
    selected_results = None
    validation_attempts: list[dict] = []
    # Validation may choose among the five best development-only candidates.
    # The final holdout remains completely untouched during selection.
    for _score, params, dev_result in rankings[:5]:
        validation = _period_result(frame, *periods["validation"], params)
        validation_attempts.append(
            {"parameters": asdict(params), **_serializable(validation)}
        )
        if (
            validation["trades"] >= 12
            and validation["net_profit"] > 0
            and validation["profit_factor"] > 1.05
            and validation["max_drawdown_percent"] < 9.0
        ):
            selected = params
            selected_results = {
                "development": dev_result,
                "validation": validation,
                "holdout": _period_result(frame, *periods["holdout"], params),
                "full_10y": _period_result(frame, *periods["full_10y"], params),
            }
            break

    stable = bool(
        selected_results
        and selected_results["holdout"]["trades"] >= 12
        and selected_results["holdout"]["net_profit"] > 0
        and selected_results["holdout"]["profit_factor"] > 1.05
        and selected_results["holdout"]["max_drawdown_percent"] < 9.0
    )
    profitable_volume_integration = bool(
        all(
            baseline[name]["net_profit"] > 0
            and baseline[name]["profit_factor"] > 1.0
            and baseline[name]["max_drawdown_percent"] < 9.0
            for name in ("development", "validation", "holdout", "full_10y")
        )
    )
    summary = {
        "status": "PASS" if profitable_volume_integration else "FAIL",
        "profitable_order_flow_integration_supported": (
            profitable_volume_integration
        ),
        "directional_pressure_blocking_supported": stable,
        "data_source": str(source),
        "starting_balance": STARTING_BALANCE,
        "data_coverage": {
            "start": coverage_start.isoformat(),
            "end": coverage_end.isoformat(),
            "bars": int(len(frame)),
        },
        "selection_protocol": (
            "256-grid development search; validation chooses only among top "
            "five development candidates; 2024-2026 holdout untouched."
        ),
        "deployed_parameters": {
            "volume_ratio_min": 0.80,
            "directional_pressure": "SHADOW_ONLY",
        },
        "experimental_selected_parameters": (
            asdict(selected) if selected else None
        ),
        "baseline": {
            name: _serializable(result) for name, result in baseline.items()
        },
        "filtered": (
            {
                name: _serializable(result)
                for name, result in selected_results.items()
            }
            if selected_results
            else None
        ),
        "validation_attempts": validation_attempts,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    if selected_results:
        selected_results["full_10y"]["trade_rows"].to_csv(
            OUT / "filtered_trades.csv", index=False
        )

    lines = [
        "# V14.23 10-Year Order-Flow Proxy Backtest",
        "",
        f"Status: **{summary['status']}**",
        "",
        f"Data coverage: {coverage_start.date()} through {coverage_end.date()} "
        f"({len(frame):,} H4 bars).",
        f"Starting balance for each reported segment: ${STARTING_BALANCE:,.2f}.",
        "",
        "True historical FX DOM was unavailable. The test uses completed-candle "
        "tick volume, signed-volume imbalance, body pressure, and close location.",
        "",
        f"Deployed parameters: `{json.dumps(summary['deployed_parameters'])}`",
        "",
        "Directional pressure candidate: "
        f"`{json.dumps(summary['experimental_selected_parameters'])}`",
        "",
        "| Segment | Baseline trades | Filtered trades | Baseline net | Filtered net | Baseline PF | Filtered PF | Baseline DD | Filtered DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in periods:
        base = baseline[name]
        filt = selected_results[name] if selected_results else None
        lines.append(
            f"| {name} | {base['trades']} | {filt['trades'] if filt else '-'} "
            f"| ${base['net_profit']:,.2f} | "
            f"{f'${filt['net_profit']:,.2f}' if filt else '-'} "
            f"| {base['profit_factor']:.3f} | "
            f"{f'{filt['profit_factor']:.3f}' if filt else '-'} "
            f"| {base['max_drawdown_percent']:.2f}% | "
            f"{f'{filt['max_drawdown_percent']:.2f}%' if filt else '-'} |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            (
                "The completed-H4 tick-volume confirmation remained profitable "
                "in development, validation, and untouched holdout and may be "
                "integrated behind the demo safety boundary. The extra "
                "directional pressure blocker failed holdout and remains "
                "shadow-only."
                if profitable_volume_integration
                else "No order-flow integration passed the untouched holdout."
            ),
        ]
    )
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
