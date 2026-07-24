"""Research-only order-flow proxy filter for the V14.21 portfolio.

Spot-FX bar history does not contain a centralized order book.  This study uses
only completed-candle information available at entry:

* directional tick-volume imbalance over 5 and 20 bars;
* candle body pressure and directional close location;
* tick-volume expansion versus its 20-bar average; and
* historical broker spread recorded on the bar.

Parameters are selected on the development partition only and then frozen for
validation and holdout.  The script never submits orders or changes live state.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_ai_bridge.app import connect  # noqa: E402
from mt5_ai_bridge.config import load_settings  # noqa: E402
from mt5_ai_bridge.gbpusd_breakout_v2 import _adx, _atr, _ema  # noqa: E402
from mt5_ai_bridge.mt5_client import create_client  # noqa: E402
from mt5_ai_bridge.v14_3_live_execution import resolve_broker_symbol  # noqa: E402
from mt5_ai_bridge.v14_3_live_signals import resolve_all_symbols  # noqa: E402
from mt5_ai_bridge.v14_3_mt5_broker_compat import (  # noqa: E402
    MT5BrokerCompatibilityClient,
)

OUT = RESEARCH / "v14_22_order_flow_filter_out"
LEDGER = (
    RESEARCH
    / "v14_3_true_combined_v12_ict_output"
    / "true_combined_closed_trades.csv"
)


@dataclass(frozen=True)
class FilterParams:
    imbalance_5_min: float
    pressure_score_min: float
    volume_ratio_min: float


def _frame(raw: Any) -> pd.DataFrame:
    frame = pd.DataFrame(raw)
    if frame.empty:
        return frame
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def _pip_size(info: Any) -> float:
    point = float(getattr(info, "point", 0.0) or 0.0)
    digits = int(getattr(info, "digits", 0) or 0)
    return point * 10.0 if digits in {3, 5} else point


def order_flow_features(
    frame: pd.DataFrame,
    duration: pd.Timedelta,
    point: float,
    pip: float,
) -> pd.DataFrame:
    result = frame.copy()
    candle_range = (result["high"] - result["low"]).replace(0.0, np.nan)
    result["available"] = result["time"] + duration
    result["body_pressure"] = (
        (result["close"] - result["open"]) / candle_range
    ).clip(-1.0, 1.0)
    result["close_location"] = (
        (result["close"] - result["low"]) / candle_range
    ).clip(0.0, 1.0)
    result["volume_ratio"] = (
        result["tick_volume"]
        / result["tick_volume"].rolling(20, min_periods=20).mean()
    )
    direction = np.sign(result["close"].diff()).fillna(0.0)
    signed_volume = direction * result["tick_volume"]
    for window in (5, 20):
        result[f"imbalance_{window}"] = (
            signed_volume.rolling(window, min_periods=window).sum()
            / result["tick_volume"]
            .rolling(window, min_periods=window)
            .sum()
            .replace(0.0, np.nan)
        )
    if "spread" in result:
        result["spread_pips"] = (
            result["spread"].astype(float) * float(point) / float(pip)
            if pip > 0
            else np.nan
        )
    else:
        result["spread_pips"] = np.nan
    return result


def _side_sign(value: Any) -> int:
    text = str(value).upper()
    if text in {"BUY", "1", "1.0"}:
        return 1
    if text in {"SELL", "-1", "-1.0"}:
        return -1
    return 1 if float(value) > 0 else -1


def attach_directional_features(
    trades: pd.DataFrame,
    features_by_symbol: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    feature_columns = [
        "available",
        "body_pressure",
        "close_location",
        "volume_ratio",
        "imbalance_5",
        "imbalance_20",
        "spread_pips",
    ]
    for symbol, group in trades.groupby("symbol", sort=False):
        feature = features_by_symbol.get(str(symbol))
        if feature is None or feature.empty:
            continue
        left = group.sort_values("entry_time").copy()
        right = feature[feature_columns].dropna(subset=["available"]).sort_values(
            "available"
        )
        joined = pd.merge_asof(
            left,
            right,
            left_on="entry_time",
            right_on="available",
            direction="backward",
        )
        rows.append(joined)
    if not rows:
        return pd.DataFrame()
    output = pd.concat(rows, ignore_index=True, sort=False)
    output["side_sign"] = output["side"].map(_side_sign)
    output["directional_imbalance_5"] = (
        output["side_sign"] * output["imbalance_5"]
    )
    output["directional_imbalance_20"] = (
        output["side_sign"] * output["imbalance_20"]
    )
    output["directional_body_pressure"] = (
        output["side_sign"] * output["body_pressure"]
    )
    output["directional_close_location"] = np.where(
        output["side_sign"] > 0,
        output["close_location"],
        1.0 - output["close_location"],
    )
    output["pressure_score"] = (
        0.35 * output["directional_imbalance_5"]
        + 0.25 * output["directional_imbalance_20"]
        + 0.20 * output["directional_body_pressure"]
        + 0.20 * (2.0 * output["directional_close_location"] - 1.0)
    )
    return output.dropna(
        subset=[
            "r_multiple",
            "directional_imbalance_5",
            "pressure_score",
            "volume_ratio",
        ]
    ).sort_values("entry_time").reset_index(drop=True)


def stats(frame: pd.DataFrame) -> dict[str, float | int | None]:
    values = frame["r_multiple"].astype(float)
    if values.empty:
        return {
            "trades": 0,
            "net_r": 0.0,
            "average_r": 0.0,
            "profit_factor": None,
            "max_drawdown_r": 0.0,
            "win_rate": 0.0,
        }
    gains = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    equity = values.cumsum()
    drawdown = equity.cummax() - equity
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "average_r": round(float(values.mean()), 6),
        "profit_factor": round(gains / losses, 6) if losses > 0 else None,
        "max_drawdown_r": round(float(drawdown.max()), 6),
        "win_rate": round(float((values > 0).mean()), 6),
    }


def apply_filter(frame: pd.DataFrame, params: FilterParams) -> pd.DataFrame:
    return frame[
        (frame["directional_imbalance_5"] >= params.imbalance_5_min)
        & (frame["pressure_score"] >= params.pressure_score_min)
        & (frame["volume_ratio"] >= params.volume_ratio_min)
    ].copy()


def partitions(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    ordered = frame.sort_values("entry_time").reset_index(drop=True)
    first = max(1, int(len(ordered) * 0.60))
    second = max(first + 1, int(len(ordered) * 0.80))
    return {
        "development": ordered.iloc[:first].copy(),
        "validation": ordered.iloc[first:second].copy(),
        "holdout": ordered.iloc[second:].copy(),
    }


def select_params(development: pd.DataFrame) -> tuple[FilterParams, list[dict]]:
    baseline = stats(development)
    candidates: list[dict] = []
    for imbalance, pressure, volume in itertools.product(
        (-0.15, -0.05, 0.0, 0.05, 0.15),
        (-0.15, -0.05, 0.0, 0.05, 0.15),
        (0.70, 0.85, 1.00, 1.15),
    ):
        params = FilterParams(imbalance, pressure, volume)
        selected = apply_filter(development, params)
        report = stats(selected)
        retention = (
            report["trades"] / baseline["trades"] if baseline["trades"] else 0.0
        )
        if report["trades"] < 30 or retention < 0.50:
            continue
        dd = max(float(report["max_drawdown_r"]), 0.25)
        score = (
            float(report["net_r"]) / dd
            * math.sqrt(retention)
            + float(report["average_r"]) * math.sqrt(report["trades"])
        )
        candidates.append(
            {
                "params": asdict(params),
                "development": report,
                "retention": round(retention, 6),
                "selection_score": round(score, 6),
            }
        )
    if not candidates:
        return FilterParams(-1.0, -1.0, 0.0), []
    candidates.sort(key=lambda item: item["selection_score"], reverse=True)
    return FilterParams(**candidates[0]["params"]), candidates


def evaluate_group(name: str, frame: pd.DataFrame) -> dict[str, Any]:
    split = partitions(frame)
    params, search = select_params(split["development"])
    reports: dict[str, Any] = {}
    stable = True
    for partition_name, sample in split.items():
        baseline = stats(sample)
        filtered_frame = apply_filter(sample, params)
        filtered = stats(filtered_frame)
        retention = (
            filtered["trades"] / baseline["trades"]
            if baseline["trades"]
            else 0.0
        )
        reports[partition_name] = {
            "baseline": baseline,
            "filtered": filtered,
            "retention": round(retention, 6),
        }
        if partition_name in {"validation", "holdout"}:
            baseline_pf = float(baseline["profit_factor"] or 0.0)
            filtered_pf = float(filtered["profit_factor"] or 0.0)
            stable = stable and bool(
                filtered["trades"] >= 30
                and retention >= 0.50
                and filtered["average_r"] > baseline["average_r"]
                and filtered_pf >= baseline_pf
                and filtered["max_drawdown_r"] <= baseline["max_drawdown_r"]
            )
    return {
        "group": name,
        "rows_with_features": int(len(frame)),
        "selected_params": asdict(params),
        "partitions": reports,
        "stable_out_of_sample_improvement": stable,
        "top_development_search": search[:10],
    }


def gold_candidates(m30: pd.DataFrame, h4: pd.DataFrame) -> pd.DataFrame:
    entry = m30.copy()
    entry["atr"] = _atr(entry, 14)
    entry["adx"] = _adx(entry, 14)
    entry["average_volume"] = entry["tick_volume"].rolling(20, min_periods=20).mean()
    entry["volume_ratio_setup"] = entry["tick_volume"] / entry["average_volume"]
    entry["channel_high"] = entry["high"].rolling(55, min_periods=55).max().shift(1)
    entry["channel_low"] = entry["low"].rolling(55, min_periods=55).min().shift(1)
    entry["available"] = entry["time"] + pd.Timedelta(minutes=30)

    trend = h4.copy()
    trend["h4_ema20"] = _ema(trend["close"], 20)
    trend["h4_ema50"] = _ema(trend["close"], 50)
    trend["available"] = trend["time"] + pd.Timedelta(hours=4)
    trend = trend[
        ["available", "close", "h4_ema20", "h4_ema50"]
    ].rename(columns={"close": "h4_close"})
    entry = pd.merge_asof(
        entry.sort_values("available"),
        trend.sort_values("available"),
        on="available",
        direction="backward",
    )
    hour_ok = entry["available"].dt.hour.isin(range(7, 18))
    common = (
        (entry["adx"] >= 15.0)
        & (entry["volume_ratio_setup"] >= 0.80)
        & hour_ok
    )
    long = (
        common
        & (entry["h4_ema20"] > entry["h4_ema50"])
        & (entry["h4_close"] > entry["h4_ema20"])
        & (entry["close"] > entry["channel_high"])
    )
    short = (
        common
        & (entry["h4_ema20"] < entry["h4_ema50"])
        & (entry["h4_close"] < entry["h4_ema20"])
        & (entry["close"] < entry["channel_low"])
    )
    indices = np.flatnonzero((long | short).fillna(False).to_numpy())
    rows: list[dict[str, Any]] = []
    for index in indices:
        if index + 1 >= len(entry):
            continue
        signal = entry.iloc[int(index)]
        side = 1 if bool(long.iloc[int(index)]) else -1
        risk = 2.0 * float(signal["atr"])
        if not np.isfinite(risk) or risk <= 0:
            continue
        open_price = float(entry.iloc[int(index) + 1]["open"])
        stop = open_price - side * risk
        target = open_price + side * 2.0 * risk
        best = open_price
        realized_r = 0.0
        exit_time = signal["available"]
        for future_index in range(
            int(index) + 1, min(len(entry), int(index) + 49)
        ):
            bar = entry.iloc[future_index]
            low, high = float(bar["low"]), float(bar["high"])
            stop_hit = low <= stop if side > 0 else high >= stop
            target_hit = high >= target if side > 0 else low <= target
            if stop_hit:
                realized_r = (stop - open_price) * side / risk
                exit_time = bar["available"]
                break
            if target_hit:
                realized_r = 2.0
                exit_time = bar["available"]
                break
            best = max(best, high) if side > 0 else min(best, low)
            favorable_r = (best - open_price) * side / risk
            if favorable_r >= 1.0:
                candidate_stop = best - side * 2.5 * float(signal["atr"])
                stop = max(stop, candidate_stop) if side > 0 else min(
                    stop, candidate_stop
                )
            realized_r = (float(bar["close"]) - open_price) * side / risk
            exit_time = bar["available"]
        rows.append(
            {
                "entry_time": signal["available"],
                "exit_time": exit_time,
                "symbol": "XAUUSD",
                "engine": "GOLD_INTRADAY_M30",
                "engine_group": "GOLD",
                "setup": "M30_BREAKOUT",
                "side": "BUY" if side > 0 else "SELL",
                "r_multiple": float(realized_r),
            }
        )
    candidates = pd.DataFrame(rows)
    if candidates.empty:
        return candidates
    # The live executor permits only one position per symbol.  Remove signals
    # that arrive while the preceding reconstructed gold trade is still open.
    accepted: list[dict[str, Any]] = []
    active_until: pd.Timestamp | None = None
    for row in candidates.sort_values("entry_time").to_dict("records"):
        entry_time = pd.Timestamp(row["entry_time"])
        if active_until is not None and entry_time < active_until:
            continue
        accepted.append(row)
        active_until = pd.Timestamp(row["exit_time"])
    return pd.DataFrame(accepted)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = MT5BrokerCompatibilityClient(create_client())
    connect(client, load_settings())
    try:
        broker_map = resolve_all_symbols(client)
        broker_map["XAUUSD"] = resolve_broker_symbol(client, "XAUUSD")
        m30_features: dict[str, pd.DataFrame] = {}
        h4_features: dict[str, pd.DataFrame] = {}
        raw_m30: dict[str, pd.DataFrame] = {}
        raw_h4: dict[str, pd.DataFrame] = {}
        for symbol, broker_symbol in broker_map.items():
            info = client.symbol_info(broker_symbol)
            point = float(getattr(info, "point", 0.0) or 0.0)
            pip = _pip_size(info)
            m30 = _frame(
                client.copy_rates_from_pos(broker_symbol, "M30", 1, 50000)
            )
            h4 = _frame(
                client.copy_rates_from_pos(broker_symbol, "H4", 1, 10000)
            )
            raw_m30[symbol] = m30
            raw_h4[symbol] = h4
            m30_features[symbol] = order_flow_features(
                m30, pd.Timedelta(minutes=30), point, pip
            )
            h4_features[symbol] = order_flow_features(
                h4, pd.Timedelta(hours=4), point, pip
            )

        ledger = pd.read_csv(LEDGER)
        ledger["entry_time"] = pd.to_datetime(ledger["entry_time"], utc=True)
        ledger["r_multiple"] = pd.to_numeric(
            ledger["r_multiple"], errors="coerce"
        )
        ict = ledger[ledger["engine_group"] == "ICT"].copy()
        v12 = ledger[ledger["engine_group"] == "V12"].copy()
        ict_enriched = attach_directional_features(ict, m30_features)
        v12_enriched = attach_directional_features(v12, h4_features)

        gold_trades = gold_candidates(
            raw_m30["XAUUSD"], raw_h4["XAUUSD"]
        )
        gold_enriched = attach_directional_features(
            gold_trades, {"XAUUSD": m30_features["XAUUSD"]}
        )

        studies = {
            "ICT_M30_CONTEXT": ict_enriched,
            "V12_H4_SIGNAL_FLOW": v12_enriched,
            "GOLD_M30_SIGNAL_FLOW": gold_enriched,
        }
        reports = {
            name: evaluate_group(name, frame)
            for name, frame in studies.items()
            if len(frame) >= 150
        }
        activation_supported = bool(reports) and all(
            report["stable_out_of_sample_improvement"]
            for report in reports.values()
        )
        summary = {
            "method": (
                "Development-only parameter selection; frozen validation and "
                "holdout; completed candles only."
            ),
            "data_limitations": [
                "Spot FX has no centralized order book.",
                "Historical DOM and quote-tick direction were unavailable.",
                "ICT uses the last completed M30 context before each M1 entry.",
                "Gold outcome replay is conservative but is an independent reconstruction.",
            ],
            "feature_formula": {
                "pressure_score": (
                    "0.35*directional_imbalance_5 + "
                    "0.25*directional_imbalance_20 + "
                    "0.20*directional_body_pressure + "
                    "0.20*directional_close_location_scaled"
                )
            },
            "reports": reports,
            "live_blocking_filter_supported": activation_supported,
            "recommendation": (
                "Eligible for controlled live shadow/forward test."
                if activation_supported
                else "Do not activate as a blocking live filter; keep shadow-only."
            ),
        }
        for name, frame in studies.items():
            frame.to_csv(
                OUT / f"{name.lower()}_enriched.csv", index=False
            )
        (OUT / "v14_22_order_flow_filter_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        lines = [
            "# V14.22 Order-Flow Proxy Filter Backtest",
            "",
            f"Live blocking supported: **{activation_supported}**",
            "",
        ]
        for name, report in reports.items():
            lines.extend(
                [
                    f"## {name}",
                    "",
                    f"- Rows with features: {report['rows_with_features']}",
                    f"- Selected parameters: `{json.dumps(report['selected_params'])}`",
                    f"- Stable validation + holdout: **{report['stable_out_of_sample_improvement']}**",
                    "",
                    "| Partition | Baseline trades | Filtered trades | Baseline PF | Filtered PF | Baseline DD(R) | Filtered DD(R) |",
                    "|---|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for partition_name, values in report["partitions"].items():
                baseline, filtered = values["baseline"], values["filtered"]
                lines.append(
                    f"| {partition_name} | {baseline['trades']} | "
                    f"{filtered['trades']} | {baseline['profit_factor']} | "
                    f"{filtered['profit_factor']} | "
                    f"{baseline['max_drawdown_r']} | "
                    f"{filtered['max_drawdown_r']} |"
                )
            lines.append("")
        lines.extend(
            [
                "## Boundary",
                "",
                summary["recommendation"],
                "Historical DOM was unavailable and is not represented by this proxy.",
            ]
        )
        (OUT / "V14_22_ORDER_FLOW_FILTER_REPORT.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, indent=2, default=str))
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
