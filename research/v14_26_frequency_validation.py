"""Chronological validation of bounded higher-frequency engine variants.

Research only.  No MT5 connection and no order transmission.  Parameters are
selected on the first 50%, checked on the next 20%, and reported on the final
untouched 30% of the same public ten-year completed-candle dataset used by the
existing V12/V13 research.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import v12_plus_validated_assets_backtest as study
import v13_expanded_assets_backtest as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_26_frequency_out"
OUT.mkdir(parents=True, exist_ok=True)


def stats(frame: pd.DataFrame, cost_r: float) -> dict:
    if frame.empty:
        return {
            "trades": 0, "net_r": 0.0, "profit_factor": 0.0,
            "max_drawdown_r": 0.0, "expectancy_r": 0.0,
        }
    values = frame["r_multiple"].astype(float) - float(cost_r)
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    equity = values.cumsum()
    drawdown = equity.cummax() - equity
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 4),
        "profit_factor": round(
            gross_profit / gross_loss if gross_loss else math.inf, 4
        ),
        "max_drawdown_r": round(float(drawdown.max()), 4),
        "expectancy_r": round(float(values.mean()), 5),
    }


def split_times(frame: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(frame["entry_time"].min())
    span = pd.Timestamp(frame["entry_time"].max()) - start
    return start + span * 0.50, start + span * 0.70


def segments(
    frame: pd.DataFrame,
    dev_end: pd.Timestamp,
    confirm_end: pd.Timestamp,
    cost_r: float,
) -> dict:
    return {
        "development": stats(frame[frame["entry_time"] < dev_end], cost_r),
        "confirmation": stats(
            frame[
                (frame["entry_time"] >= dev_end)
                & (frame["entry_time"] < confirm_end)
            ],
            cost_r,
        ),
        "holdout": stats(frame[frame["entry_time"] >= confirm_end], cost_r),
        "all": stats(frame, cost_r),
    }


def selection_passes(report: dict, *, minimum=(20, 8, 10)) -> bool:
    for name, count in zip(("development", "confirmation"), minimum[:2]):
        section = report[name]
        if (
            section["trades"] < count
            or section["net_r"] <= 0
            or section["profit_factor"] < 1.05
        ):
            return False
    return True


def holdout_passes(report: dict, *, minimum=(20, 8, 10)) -> bool:
    section = report["holdout"]
    return bool(
        section["trades"] >= minimum[2]
        and section["net_r"] > 0
        and section["profit_factor"] >= 1.05
    )


def eur_candidates(h4: pd.DataFrame, params: dict) -> pd.DataFrame:
    high = h4["high"].rolling(
        params["channel"], min_periods=params["channel"]
    ).max().shift(1)
    low = h4["low"].rolling(
        params["channel"], min_periods=params["channel"]
    ).min().shift(1)
    long = (
        (h4["dclose"] > h4["dema20"])
        & (h4["dema20"] > h4["dema50"])
        & (h4["close"] > h4["ema20"])
        & (h4["adx14"] >= params["adx"])
        & (h4["close"] > high)
        & (h4["ema_sep_atr"] <= params["max_ema_sep"])
        & (h4["directional_di_gap_long"] >= params["di_gap"])
    )
    short = (
        (h4["dclose"] < h4["dema20"])
        & (h4["dema20"] < h4["dema50"])
        & (h4["close"] < h4["ema20"])
        & (h4["adx14"] >= params["adx"])
        & (h4["close"] < low)
        & (h4["ema_sep_atr"] <= params["max_ema_sep"])
        & (h4["directional_di_gap_short"] >= params["di_gap"])
    )
    rows = []
    for index in np.flatnonzero((long | short).to_numpy()):
        side = 1 if bool(long.iloc[index]) else -1
        exit_time, result_r = study._simulate(
            h4, int(index), side, 1.25, 3.0, 2.5, 24
        )
        rows.append({
            "entry_time": pd.Timestamp(h4.iloc[index]["end"]),
            "exit_time": exit_time,
            "r_multiple": float(result_r),
        })
    return pd.DataFrame(rows)


def aud_candidates(h4: pd.DataFrame, params: study.AUDUSDParams) -> pd.DataFrame:
    result = study._audusd_candidates(
        h4, params, include_unresolved=False
    )
    return result[["entry_time", "exit_time", "r_multiple"]].copy()


def prepare_gold() -> pd.DataFrame:
    m30 = base.load_frame("XAUUSD", "m30").copy()
    h4 = base.load_frame("XAUUSD", "h4").copy()
    m30["atr14"] = base.atr(m30)
    m30["plus_di"], m30["minus_di"], m30["adx14"] = base.directional(m30)
    m30["avg_volume"] = m30["tick_volume"].rolling(
        20, min_periods=20
    ).mean()
    m30["volume_ratio"] = m30["tick_volume"] / m30["avg_volume"]
    m30["end"] = m30["time"] + pd.Timedelta(minutes=30)
    h4["ema20"] = base.ema(h4["close"], 20)
    h4["ema50"] = base.ema(h4["close"], 50)
    h4["available"] = h4["time"] + pd.Timedelta(hours=4)
    context = h4[["available", "close", "ema20", "ema50"]].rename(
        columns={
            "close": "h4_close",
            "ema20": "h4_ema20",
            "ema50": "h4_ema50",
        }
    )
    return pd.merge_asof(
        m30.sort_values("end"),
        context.sort_values("available"),
        left_on="end",
        right_on="available",
        direction="backward",
    )


def simulate_gold(
    frame: pd.DataFrame, index: int, side: int
) -> tuple[pd.Timestamp, float]:
    if index + 1 >= len(frame):
        return pd.Timestamp(frame.iloc[index]["end"]), 0.0
    signal = frame.iloc[index]
    entry_row = frame.iloc[index + 1]
    entry = float(entry_row["open"])
    risk = float(signal["atr14"]) * 2.0
    stop = entry - side * risk
    target = entry + side * 2.0 * risk
    best_stop = stop
    last_index = min(len(frame) - 1, index + 48)
    for cursor in range(index + 1, last_index + 1):
        row = frame.iloc[cursor]
        low, high = float(row["low"]), float(row["high"])
        stop_hit = low <= best_stop if side > 0 else high >= best_stop
        target_hit = high >= target if side > 0 else low <= target
        if stop_hit:
            return (
                pd.Timestamp(row["end"]),
                float((best_stop - entry) * side / risk),
            )
        if target_hit:
            return pd.Timestamp(row["end"]), 2.0
        favorable = high - entry if side > 0 else entry - low
        if favorable >= risk and np.isfinite(row["atr14"]):
            candidate = float(row["close"] - side * 2.5 * row["atr14"])
            candidate = max(candidate, entry) if side > 0 else min(
                candidate, entry
            )
            best_stop = max(best_stop, candidate) if side > 0 else min(
                best_stop, candidate
            )
    final = frame.iloc[last_index]
    return (
        pd.Timestamp(final["end"]),
        float((float(final["close"]) - entry) * side / risk),
    )


def gold_candidates(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    high = frame["high"].rolling(
        params["channel"], min_periods=params["channel"]
    ).max().shift(1)
    low = frame["low"].rolling(
        params["channel"], min_periods=params["channel"]
    ).min().shift(1)
    hour = frame["end"].dt.hour
    in_session = (hour >= 7) & (hour < 18)
    long_regime = (
        (frame["h4_ema20"] > frame["h4_ema50"])
        & (frame["h4_close"] > frame["h4_ema20"])
    )
    short_regime = (
        (frame["h4_ema20"] < frame["h4_ema50"])
        & (frame["h4_close"] < frame["h4_ema20"])
    )
    common = (
        in_session
        & (frame["adx14"] >= params["adx"])
        & (frame["volume_ratio"] >= params["volume"])
    )
    long = common & long_regime & (frame["close"] > high)
    short = common & short_regime & (frame["close"] < low)
    rows = []
    for index in np.flatnonzero((long | short).to_numpy()):
        side = 1 if bool(long.iloc[index]) else -1
        exit_time, result_r = simulate_gold(frame, int(index), side)
        rows.append({
            "entry_time": pd.Timestamp(frame.iloc[index]["end"]),
            "exit_time": exit_time,
            "r_multiple": float(result_r),
        })
    return pd.DataFrame(rows)


def select(
    name: str,
    variants: list[tuple[dict, pd.DataFrame]],
    baseline: tuple[dict, pd.DataFrame],
    *,
    cost_r: float,
    minimum=(20, 8, 10),
) -> dict:
    all_times = pd.concat(
        [frame[["entry_time"]] for _params, frame in variants if not frame.empty]
    )
    dev_end, confirm_end = split_times(all_times)
    baseline_report = segments(
        baseline[1], dev_end, confirm_end, cost_r
    )
    reports = []
    for params, frame in variants:
        report = segments(frame, dev_end, confirm_end, cost_r)
        report["params"] = params
        report["selection_passed"] = selection_passes(
            report, minimum=minimum
        )
        reports.append(report)
    selectable = [
        report
        for report in reports
        if report["selection_passed"]
        and report["all"]["trades"] > baseline_report["all"]["trades"]
    ]
    selectable.sort(
        key=lambda report: (
            min(
                report["development"]["expectancy_r"],
                report["confirmation"]["expectancy_r"],
            )
            * math.sqrt(
                report["development"]["trades"]
                + report["confirmation"]["trades"]
            ),
            (
                report["development"]["trades"]
                + report["confirmation"]["trades"]
            ),
        ),
        reverse=True,
    )
    frozen = selectable[0] if selectable else None
    if frozen is not None:
        frozen["holdout_passed"] = holdout_passes(
            frozen, minimum=minimum
        )
    selected = (
        frozen
        if frozen is not None and frozen["holdout_passed"]
        else None
    )
    return {
        "engine": name,
        "development_end": dev_end.isoformat(),
        "confirmation_end": confirm_end.isoformat(),
        "cost_stress_r_per_trade": cost_r,
        "baseline": {
            "params": baseline[0],
            **baseline_report,
        },
        "selection_candidate": frozen,
        "selected": selected,
        "eligible_variants": len(selectable),
        "tested_variants": len(reports),
        "top_eligible": selectable[:10],
    }


def main() -> None:
    _eur_h1, eur_h4, _eur_d1 = study._prepare("EURUSD")
    eur_base_params = {
        "channel": 55, "adx": 20.0, "di_gap": 17.0,
        "max_ema_sep": 1.30,
    }
    eur_variants = []
    for channel, adx, gap, separation in itertools.product(
        (34, 55), (17.0, 20.0), (14.0, 17.0), (1.30, 1.50)
    ):
        params = {
            "channel": channel, "adx": adx, "di_gap": gap,
            "max_ema_sep": separation,
        }
        eur_variants.append((params, eur_candidates(eur_h4, params)))
    eur_result = select(
        "EURUSD_SWING_CORE",
        eur_variants,
        (eur_base_params, eur_candidates(eur_h4, eur_base_params)),
        cost_r=0.03,
    )

    _aud_h1, aud_h4, _aud_d1 = study._prepare("AUDUSD")
    aud_base = study.AUDUSDParams(15.0, 0.30, 0.25)
    aud_variants = []
    for hours, adx, touch, body in itertools.product(
        ((4, 8), (4, 8, 12), (0, 4, 8, 12)),
        (13.0, 15.0),
        (0.30, 0.40),
        (0.20, 0.25),
    ):
        params = study.AUDUSDParams(
            adx, touch, body, allowed_hours=hours
        )
        aud_variants.append((asdict(params), aud_candidates(aud_h4, params)))
    aud_result = select(
        "AUDUSD_TREND_PULLBACK",
        aud_variants,
        (asdict(aud_base), aud_candidates(aud_h4, aud_base)),
        cost_r=0.04,
    )

    gold = prepare_gold()
    gold_base_params = {"channel": 55, "adx": 15.0, "volume": 0.80}
    gold_variants = []
    for channel, adx, volume in itertools.product(
        (34, 55), (13.0, 15.0), (0.70, 0.80)
    ):
        params = {"channel": channel, "adx": adx, "volume": volume}
        gold_variants.append((params, gold_candidates(gold, params)))
    gold_result = select(
        "GOLD_INTRADAY_M30",
        gold_variants,
        (gold_base_params, gold_candidates(gold, gold_base_params)),
        cost_r=0.05,
        minimum=(30, 10, 15),
    )

    result = {
        "method": (
            "50% development / 20% confirmation / 30% untouched holdout; "
            "completed candles; fixed exits; per-trade R cost stress"
        ),
        "EURUSD": eur_result,
        "AUDUSD": aud_result,
        "XAUUSD": gold_result,
    }
    (OUT / "validation.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    summary = []
    for symbol in ("EURUSD", "AUDUSD", "XAUUSD"):
        item = result[symbol]
        summary.append({
            "symbol": symbol,
            "tested_variants": item["tested_variants"],
            "eligible_variants": item["eligible_variants"],
            "baseline_trades": item["baseline"]["all"]["trades"],
            "baseline_pf": item["baseline"]["all"]["profit_factor"],
            "selected_params": (
                json.dumps(item["selected"]["params"], sort_keys=True)
                if item["selected"]
                else None
            ),
            "selected_trades": (
                item["selected"]["all"]["trades"]
                if item["selected"]
                else None
            ),
            "selected_pf": (
                item["selected"]["all"]["profit_factor"]
                if item["selected"]
                else None
            ),
            "selected_holdout_pf": (
                item["selected"]["holdout"]["profit_factor"]
                if item["selected"]
                else None
            ),
            "selected_holdout_net_r": (
                item["selected"]["holdout"]["net_r"]
                if item["selected"]
                else None
            ),
        })
    pd.DataFrame(summary).to_csv(OUT / "summary.csv", index=False)
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    main()
