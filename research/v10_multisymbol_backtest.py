"""Synchronized V10 multi-symbol live-policy replay.

This replay applies the same allocations, position count, open-risk and GBP
currency caps as the live controller. It uses the frozen candidate ledger, so
it is not a new tick-level/OHLC simulation.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

ALLOWED_GBPUSD_HOURS = frozenset({7, 10, 11, 12, 14, 15, 16})
RISK = {
    "GBPUSD_SATELLITE_V2": 0.30,
    "EURUSD_SATELLITE_V7": 0.35,
    "GBPJPY_SATELLITE_V7": 0.35,
    "GBPUSD_SWING_V6": 0.40,
}


@dataclass(frozen=True)
class Metrics:
    starting_balance: float
    ending_balance: float
    net_profit: float
    return_percent: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    average_trade: float
    realized_drawdown_percent: float
    stress_drawdown_percent: float


def _load_h4_precision(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=None, engine="python").rename(columns={
        "<DATE>": "date", "<TIME>": "clock", "<OPEN>": "open",
        "<HIGH>": "high", "<LOW>": "low", "<CLOSE>": "close",
        "<TICKVOL>": "tick_volume",
    })
    frame["time"] = pd.to_datetime(
        frame["date"].astype(str) + " " + frame["clock"].astype(str),
        format="%Y.%m.%d %H:%M:%S", utc=True,
    )
    previous = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    frame["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    frame["ema20"] = frame["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    frame["ema50"] = frame["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    frame["volume_ratio"] = frame["tick_volume"] / frame["tick_volume"].rolling(20, min_periods=20).mean()
    frame["atr_ratio"] = frame["atr14"] / frame["atr14"].rolling(20, min_periods=20).mean()
    frame["range_atr"] = (frame["high"] - frame["low"]) / frame["atr14"]
    return frame


def _apply_swing_precision(frame: pd.DataFrame, h4_path: Path | None) -> pd.DataFrame:
    frame = frame.copy()
    frame["precision_allowed"] = True
    frame["precision_grade"] = "NOT_APPLICABLE"
    if h4_path is None:
        return frame
    h4 = _load_h4_precision(h4_path)
    swing_mask = frame["engine"].eq("GBPUSD_SWING_V6")
    swing = frame[swing_mask].copy()
    if swing.empty:
        return frame
    swing["signal_bar_time"] = swing["entry_time"] - pd.Timedelta(hours=8)
    swing = swing.merge(
        h4[["time", "open", "close", "atr14", "ema20", "ema50",
            "volume_ratio", "atr_ratio", "range_atr"]],
        left_on="signal_bar_time", right_on="time", how="left",
        validate="many_to_one",
    )
    if swing["time"].isna().any():
        raise ValueError("Unable to match a GBPUSD swing candidate to its H4 signal bar")
    for original_index, row in swing.iterrows():
        directional_body_atr = int(row["side"]) * (row["close"] - row["open"]) / row["atr14"]
        directional_gap_atr = int(row["side"]) * (row["ema20"] - row["ema50"]) / row["atr14"]
        setup = str(row["setup"]).upper()
        allowed, grade, risk = True, "B", 0.20
        if "PRIMARY" in setup:
            strong = row["volume_ratio"] >= 1.248 and row["range_atr"] >= 1.555
            grade, risk = ("A", 0.40) if strong else ("B", 0.15)
        elif "SECONDARY" in setup:
            allowed = row["atr_ratio"] >= 1.018 and directional_body_atr <= 1.473
            grade, risk = ("A", 0.40) if allowed else ("REJECT", 0.0)
        elif "PULLBACK" in setup:
            allowed = directional_gap_atr <= 1.237
            grade, risk = ("A", 0.40) if allowed else ("REJECT", 0.0)
        target_index = swing.index.get_loc(original_index)
        source_index = frame[swing_mask].index[target_index]
        frame.loc[source_index, "precision_allowed"] = bool(allowed)
        frame.loc[source_index, "precision_grade"] = grade
        frame.loc[source_index, "risk_percent"] = risk
    return frame


def load_candidates(accepted: Path, rejected: Path, gbpusd_h4: Path | None = None) -> pd.DataFrame:
    a = pd.read_csv(accepted)
    r = pd.read_csv(rejected)
    for frame in (a, r):
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    a["source"] = "accepted"
    a["source_priority"] = 0
    a["original_order"] = range(len(a))
    r["source"] = "rejected"
    r["source_priority"] = 1
    r["original_order"] = pd.NA
    frame = pd.concat([a, r], ignore_index=True, sort=False).sort_values(
        ["entry_time", "source_priority", "original_order", "id"],
        na_position="last",
    ).reset_index(drop=True)
    frame["candidate_order"] = range(len(frame))
    for engine, risk in RISK.items():
        frame.loc[frame["engine"] == engine, "risk_percent"] = risk
    return _apply_swing_precision(frame, gbpusd_h4)


def strategy_allowed(row: pd.Series) -> bool:
    if not bool(row.get("precision_allowed", True)):
        return False
    if row["engine"] == "GBPUSD_SATELLITE_V2":
        return int(row["entry_time"].hour) in ALLOWED_GBPUSD_HOURS
    return True


def gbp_side(symbol: str, side: int) -> int:
    return int(side) if str(symbol).upper().startswith("GBP") else 0


def replay(
    candidates: pd.DataFrame,
    *,
    initial_balance: float = 5_000.0,
    max_positions: int = 3,
    max_open_risk_percent: float = 0.75,
    aligned_gbp_cap_percent: float = 0.75,
    mixed_gbp_cap_percent: float = 0.50,
    additional_cost_r: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict], list[dict]]:
    events = []
    for index, row in candidates.iterrows():
        order = int(row["candidate_order"])
        events.append((row["entry_time"], 1, order, index, "entry"))
        exit_priority = 2 if row["exit_time"] == row["entry_time"] else 0
        events.append((row["exit_time"], exit_priority, order, index, "exit"))
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    balance = float(initial_balance)
    open_positions: dict[int, dict] = {}
    accepted: list[dict] = []
    rejected: list[dict] = []
    realized = [{"time": None, "equity": balance}]
    stress = [{"time": None, "equity": balance}]

    for event_time, _priority, _order, index, kind in events:
        row = candidates.loc[index]
        if kind == "exit":
            position = open_positions.pop(index, None)
            if position is not None:
                balance += position["pnl"]
                position["exit_balance"] = balance
                accepted.append(position)
            realized.append({"time": event_time.isoformat(), "equity": balance})
            stress.append({
                "time": event_time.isoformat(),
                "equity": balance - sum(item["risk_dollars"] for item in open_positions.values()),
            })
            continue

        identity = {
            "symbol": str(row["symbol"]),
            "engine": str(row["engine"]),
            "setup": str(row["setup"]),
            "side": int(row["side"]),
            "entry_time": row["entry_time"].isoformat(),
            "exit_time": row["exit_time"].isoformat(),
            "risk_percent": float(row["risk_percent"]),
            "source": str(row["source"]),
        }
        if not strategy_allowed(row):
            reason = "swing_precision_filter" if not bool(row.get("precision_allowed", True)) else "strategy_hour_filter"
            rejected.append({"reason": reason, **identity})
            continue

        risk_dollars = balance * float(row["risk_percent"]) / 100.0
        open_risk = sum(item["risk_dollars"] for item in open_positions.values())
        reason = None
        if len(open_positions) >= max_positions:
            reason = "max_positions"
        elif open_risk + risk_dollars > balance * max_open_risk_percent / 100.0 + 1e-9:
            reason = "max_open_risk"

        if reason is None and str(row["symbol"]).upper().startswith("GBP"):
            gbp_positions = [
                item for item in open_positions.values()
                if str(item["symbol"]).upper().startswith("GBP")
            ]
            gbp_risk = sum(item["risk_dollars"] for item in gbp_positions)
            sides = {gbp_side(item["symbol"], item["side"]) for item in gbp_positions}
            sides.add(gbp_side(str(row["symbol"]), int(row["side"])))
            mixed = len({value for value in sides if value}) > 1
            cap = mixed_gbp_cap_percent if mixed else aligned_gbp_cap_percent
            if gbp_risk + risk_dollars > balance * cap / 100.0 + 1e-9:
                reason = "gbp_currency_risk_cap"

        if reason is not None:
            rejected.append({"reason": reason, **identity})
            continue
        r_multiple = float(row["r_multiple"]) - additional_cost_r
        position = {
            **identity,
            "risk_dollars": risk_dollars,
            "r_multiple": r_multiple,
            "pnl": risk_dollars * r_multiple,
            "entry_balance": balance,
        }
        open_positions[index] = position
        realized.append({"time": event_time.isoformat(), "equity": balance})
        stress.append({
            "time": event_time.isoformat(),
            "equity": balance - sum(item["risk_dollars"] for item in open_positions.values()),
        })

    if open_positions:
        raise RuntimeError("Replay ended with open positions")
    return pd.DataFrame(accepted), pd.DataFrame(rejected), realized, stress


def max_drawdown(curve: list[dict]) -> float:
    peak = float(curve[0]["equity"])
    maximum = 0.0
    for point in curve:
        value = float(point["equity"])
        peak = max(peak, value)
        maximum = max(maximum, (peak - value) / peak * 100 if peak else 0.0)
    return maximum


def metrics(trades: pd.DataFrame, realized: list[dict], stress: list[dict]) -> Metrics:
    pnl = trades["pnl"] if len(trades) else pd.Series(dtype=float)
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    ending = 5_000.0 + float(pnl.sum())
    return Metrics(
        5_000.0,
        ending,
        ending - 5_000.0,
        (ending - 5_000.0) / 5_000.0 * 100,
        int(len(trades)),
        int((pnl > 0).sum()),
        int((pnl <= 0).sum()),
        float((pnl > 0).mean()) if len(pnl) else 0.0,
        gross_profit / gross_loss if gross_loss else 0.0,
        float(pnl.mean()) if len(pnl) else 0.0,
        max_drawdown(realized),
        max_drawdown(stress),
    )


def engine_summary(trades: pd.DataFrame) -> list[dict]:
    rows = []
    for engine, group in trades.groupby("engine", sort=True):
        pnl = group["pnl"]
        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
        rows.append({
            "engine": str(engine),
            "trades": int(len(group)),
            "net_profit": float(pnl.sum()),
            "win_rate": float((pnl > 0).mean()),
            "profit_factor": gross_profit / gross_loss if gross_loss else 0.0,
        })
    return rows


def run(accepted_path: Path, rejected_path: Path, output: Path, gbpusd_h4: Path | None = None) -> dict:
    candidates = load_candidates(accepted_path, rejected_path, gbpusd_h4)
    trades, rejected, realized, stress = replay(candidates)
    result = {
        "methodology": "Synchronized candidate-ledger replay using the V10 multi-symbol live risk policy and completed-H4 GBPUSD swing precision gate.",
        "period": {
            "start": candidates["entry_time"].min().isoformat(),
            "end": candidates["exit_time"].max().isoformat(),
        },
        "metrics": asdict(metrics(trades, realized, stress)),
        "engine_summary": engine_summary(trades),
        "rejections": rejected["reason"].value_counts().to_dict() if len(rejected) else {},
        "cost_stress": {},
        "limitations": [
            "This is a synchronized ledger replay, not a fresh tick/OHLC simulation.",
            "EURUSD and GBPJPY candidate history is approximately one year, not ten years.",
            "Live broker spread, slippage and order rejection are represented only by R-cost stress.",
            "The GBPUSD precision gate is applied to the synchronized swing candidates using uploaded completed H4 bars.",
        ],
    }
    for cost in (0.03, 0.05, 0.10):
        stressed, _, rcurve, scurve = replay(candidates, additional_cost_r=cost)
        result["cost_stress"][f"{cost:.2f}R"] = asdict(metrics(stressed, rcurve, scurve))
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "accepted_trades.csv", index=False)
    rejected.to_csv(output / "rejected_candidates.csv", index=False)
    pd.DataFrame(realized).to_csv(output / "realized_equity.csv", index=False)
    pd.DataFrame(stress).to_csv(output / "stress_equity.csv", index=False)
    (output / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("accepted", type=Path)
    parser.add_argument("rejected", type=Path)
    parser.add_argument("--output", type=Path, default=Path("v10_multisymbol_results"))
    parser.add_argument("--gbpusd-h4", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.accepted, args.rejected, args.output, args.gbpusd_h4), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
