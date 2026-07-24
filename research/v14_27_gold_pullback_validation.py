"""Separate H4/M30/M15 Gold trend-pullback validation.

Parameters are selected on the first 50% plus a 20% confirmation segment.
The final 30% is opened once as a promotion gate. Completed candles only;
research code never connects to MT5 or sends orders.
"""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import v13_expanded_assets_backtest as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_27_gold_pullback_out"
OUT.mkdir(parents=True, exist_ok=True)
COST_R = 0.07


def prepare() -> tuple[pd.DataFrame, pd.DataFrame]:
    m15 = base.load_frame("XAUUSD", "m15").copy()
    m30 = base.load_frame("XAUUSD", "m30").copy()
    h4 = base.load_frame("XAUUSD", "h4").copy()
    for frame, minutes in ((m15, 15), (m30, 30)):
        frame["atr14"] = base.atr(frame)
        frame["ema20"] = base.ema(frame["close"], 20)
        frame["ema50"] = base.ema(frame["close"], 50)
        frame["range"] = frame["high"] - frame["low"]
        frame["body_atr"] = (
            (frame["close"] - frame["open"]).abs()
            / frame["atr14"].replace(0, np.nan)
        )
        frame["close_location"] = (
            (frame["close"] - frame["low"])
            / frame["range"].replace(0, np.nan)
        )
        frame["end"] = frame["time"] + pd.Timedelta(minutes=minutes)
    m30["plus_di"], m30["minus_di"], m30["adx14"] = base.directional(m30)
    m30["avg_volume"] = m30["tick_volume"].rolling(
        20, min_periods=20
    ).mean()
    m30["volume_ratio"] = m30["tick_volume"] / m30["avg_volume"]
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
    m30 = pd.merge_asof(
        m30.sort_values("end"),
        context.sort_values("available"),
        left_on="end",
        right_on="available",
        direction="backward",
    )
    return m15.reset_index(drop=True), m30.reset_index(drop=True)


def simulate(
    m15: pd.DataFrame,
    signal_end: pd.Timestamp,
    side: int,
    risk_price: float,
    target_r: float,
    max_bars: int = 48,
) -> tuple[pd.Timestamp, float]:
    start = int(m15["time"].searchsorted(signal_end, side="left"))
    if start >= len(m15) or not np.isfinite(risk_price) or risk_price <= 0:
        return signal_end, 0.0
    entry = float(m15.iloc[start]["open"])
    stop = entry - side * risk_price
    target = entry + side * target_r * risk_price
    final_index = min(len(m15) - 1, start + max_bars - 1)
    for index in range(start, final_index + 1):
        row = m15.iloc[index]
        stop_hit = (
            float(row["low"]) <= stop
            if side > 0
            else float(row["high"]) >= stop
        )
        target_hit = (
            float(row["high"]) >= target
            if side > 0
            else float(row["low"]) <= target
        )
        # Conservative same-bar ordering.
        if stop_hit:
            return pd.Timestamp(row["end"]), -1.0
        if target_hit:
            return pd.Timestamp(row["end"]), float(target_r)
    final = m15.iloc[final_index]
    return (
        pd.Timestamp(final["end"]),
        float((float(final["close"]) - entry) * side / risk_price),
    )


def candidates(
    m15: pd.DataFrame, m30: pd.DataFrame, params: dict
) -> pd.DataFrame:
    confirmation_columns = [
        "open", "close", "atr14", "ema20", "body_atr", "close_location"
    ]
    confirmations = (
        m15.drop_duplicates("end", keep="last")
        .set_index("end")[confirmation_columns]
        .reindex(m30["end"])
        .reset_index(drop=True)
    )
    required_columns = [
        "atr14", "ema20", "ema50", "adx14", "volume_ratio", "h4_close",
        "h4_ema20", "h4_ema50",
    ]
    finite = np.isfinite(m30[required_columns]).all(axis=1)
    finite &= np.isfinite(
        confirmations[["atr14", "ema20", "body_atr", "close_location"]]
    ).all(axis=1)
    signal_hours = m30["end"].dt.hour
    common = (
        finite
        & signal_hours.between(7, 17)
        & (m30["adx14"] >= params["adx"])
        & (m30["volume_ratio"] >= params["volume"])
        & (m30["body_atr"] >= params["m30_body"])
    )
    atr_value = m30["atr14"]
    long_bias = (
        (m30["h4_close"] > m30["h4_ema20"])
        & (m30["h4_ema20"] > m30["h4_ema50"])
        & (m30["ema20"] > m30["ema50"])
    )
    short_bias = (
        (m30["h4_close"] < m30["h4_ema20"])
        & (m30["h4_ema20"] < m30["h4_ema50"])
        & (m30["ema20"] < m30["ema50"])
    )
    long_pullback = (
        common
        & long_bias
        & (m30["low"] <= m30["ema20"] + params["touch_atr"] * atr_value)
        & (m30["low"] >= m30["ema50"] - 0.30 * atr_value)
        & (m30["close"] > m30["ema20"])
        & (m30["close"] > m30["open"])
    )
    short_pullback = (
        common
        & short_bias
        & (m30["high"] >= m30["ema20"] - params["touch_atr"] * atr_value)
        & (m30["high"] <= m30["ema50"] + 0.30 * atr_value)
        & (m30["close"] < m30["ema20"])
        & (m30["close"] < m30["open"])
    )
    m15_long = (
        (confirmations["close"] > confirmations["open"])
        & (confirmations["close"] > confirmations["ema20"])
        & (confirmations["body_atr"] >= params["m15_body"])
        & (confirmations["close_location"] >= 0.60)
    )
    m15_short = (
        (confirmations["close"] < confirmations["open"])
        & (confirmations["close"] < confirmations["ema20"])
        & (confirmations["body_atr"] >= params["m15_body"])
        & (confirmations["close_location"] <= 0.40)
    )
    sides = np.select(
        [long_pullback & m15_long, short_pullback & m15_short],
        [1, -1],
        default=0,
    )
    rows = []
    blocked_until: pd.Timestamp | None = None
    eligible_indices = np.flatnonzero(sides)
    eligible_indices = eligible_indices[
        (eligible_indices >= 60) & (eligible_indices < len(m30) - 1)
    ]
    for index in eligible_indices:
        row = m30.iloc[index]
        signal_end = pd.Timestamp(row["end"])
        if blocked_until is not None and signal_end < blocked_until:
            continue
        side = int(sides[index])
        risk_atr = float(row["atr14"])
        exit_time, result_r = simulate(
            m15,
            signal_end,
            side,
            params["stop_atr"] * risk_atr,
            params["target_r"],
        )
        rows.append({
            "entry_time": signal_end,
            "exit_time": exit_time,
            "side": side,
            "r_multiple": float(result_r),
        })
        blocked_until = exit_time
    return pd.DataFrame(rows)


def stats(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "trades": 0, "net_r": 0.0, "profit_factor": 0.0,
            "max_drawdown_r": 0.0, "expectancy_r": 0.0,
        }
    values = frame["r_multiple"].astype(float) - COST_R
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


def report(
    frame: pd.DataFrame,
    development_end: pd.Timestamp,
    confirmation_end: pd.Timestamp,
) -> dict:
    return {
        "development": stats(
            frame[frame["entry_time"] < development_end]
        ),
        "confirmation": stats(
            frame[
                (frame["entry_time"] >= development_end)
                & (frame["entry_time"] < confirmation_end)
            ]
        ),
        "holdout": stats(
            frame[frame["entry_time"] >= confirmation_end]
        ),
        "all": stats(frame),
    }


def main() -> None:
    m15, m30 = prepare()
    start, end = m30["time"].min(), m30["time"].max()
    development_end = start + (end - start) * 0.50
    confirmation_end = start + (end - start) * 0.70
    tested = []
    for touch, adx, volume, m30_body, m15_body, target in itertools.product(
        (0.20, 0.40, 0.60),
        (15.0, 18.0),
        (0.70, 0.80),
        (0.15, 0.25),
        (0.10, 0.20),
        (1.5, 2.0),
    ):
        params = {
            "touch_atr": touch,
            "adx": adx,
            "volume": volume,
            "m30_body": m30_body,
            "m15_body": m15_body,
            "stop_atr": 1.5,
            "target_r": target,
        }
        frame = candidates(m15, m30, params)
        item = {"params": params, **report(
            frame, development_end, confirmation_end
        )}
        item["selection_passed"] = bool(
            item["development"]["trades"] >= 40
            and item["confirmation"]["trades"] >= 15
            and item["development"]["net_r"] > 0
            and item["confirmation"]["net_r"] > 0
            and item["development"]["profit_factor"] >= 1.10
            and item["confirmation"]["profit_factor"] >= 1.10
        )
        tested.append(item)
    selectable = [item for item in tested if item["selection_passed"]]
    selectable.sort(
        key=lambda item: (
            min(
                item["development"]["expectancy_r"],
                item["confirmation"]["expectancy_r"],
            )
            * math.sqrt(
                item["development"]["trades"]
                + item["confirmation"]["trades"]
            ),
            item["development"]["trades"]
            + item["confirmation"]["trades"],
        ),
        reverse=True,
    )
    frozen = selectable[0] if selectable else None
    promoted = False
    if frozen is not None:
        holdout = frozen["holdout"]
        promoted = bool(
            holdout["trades"] >= 20
            and holdout["net_r"] > 0
            and holdout["profit_factor"] >= 1.10
        )
    result = {
        "method": (
            "50% development / 20% confirmation parameter selection; "
            "30% untouched holdout promotion; completed M15/M30/H4 candles"
        ),
        "cost_stress_r_per_trade": COST_R,
        "tested_variants": len(tested),
        "selection_eligible": len(selectable),
        "selection_candidate": frozen,
        "promoted": promoted,
        "selected": frozen if promoted else None,
        "top_selection_candidates": selectable[:10],
    }
    (OUT / "validation.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({
        "tested_variants": result["tested_variants"],
        "selection_eligible": result["selection_eligible"],
        "promoted": promoted,
        "params": frozen["params"] if frozen else None,
        "development": frozen["development"] if frozen else None,
        "confirmation": frozen["confirmation"] if frozen else None,
        "holdout": frozen["holdout"] if frozen else None,
        "all": frozen["all"] if frozen else None,
    }, indent=2))


if __name__ == "__main__":
    main()
