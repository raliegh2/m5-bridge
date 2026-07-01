"""Multi-year walk-forward harness for the GBPUSD regime-adaptive swing engine.

This runner does not download data. It validates that at least five years of H4
and D1 history are supplied, derives completed weekly bars from D1 when a W1 CSV
is not provided, executes rolling train/test windows, and writes summary CSVs.

Expected columns: time, open, high, low, close, tick_volume.

Example:
    python research/gbpusd_walk_forward_runner.py \
        --h4 data/GBPUSD_H4_2016_2026.csv \
        --d1 data/GBPUSD_D1_2016_2026.csv \
        --engine research/gbpusd_regime_adaptive_swing.py \
        --years-train 4 --years-test 1
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import tempfile

import pandas as pd


def load_prices(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    required = {"time", "open", "high", "low", "close", "tick_volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def derive_weekly(d1: pd.DataFrame) -> pd.DataFrame:
    x = d1.set_index("time").sort_index()
    weekly = x.resample("W-FRI", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
    )
    return weekly.dropna().reset_index()


def import_engine(path: Path):
    spec = importlib.util.spec_from_file_location("gbpusd_engine", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import engine: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise AttributeError("Engine module must expose run(h4, d1, w1, news=None)")
    return module


def save_slice(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, path: Path) -> None:
    subset = df[(df.time >= start) & (df.time < end)].copy()
    subset.to_csv(path, index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--h4", type=Path, required=True)
    parser.add_argument("--d1", type=Path, required=True)
    parser.add_argument("--w1", type=Path)
    parser.add_argument("--news", type=Path)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--years-train", type=int, default=4)
    parser.add_argument("--years-test", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("walk_forward_results.csv"))
    args = parser.parse_args()

    h4 = load_prices(args.h4)
    d1 = load_prices(args.d1)
    w1 = load_prices(args.w1) if args.w1 else derive_weekly(d1)

    start = max(h4.time.min(), d1.time.min(), w1.time.min())
    end = min(h4.time.max(), d1.time.max(), w1.time.max())
    years = (end - start).days / 365.25
    if years < 5:
        raise ValueError(
            f"Only {years:.2f} years overlap. At least 5 years are required. "
            "Export more GBPUSD H4 and D1 history before running this test."
        )

    engine = import_engine(args.engine)
    rows = []
    window_start = start
    train = pd.DateOffset(years=args.years_train)
    test = pd.DateOffset(years=args.years_test)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        while window_start + train + test <= end:
            train_end = window_start + train
            test_end = train_end + test

            h4_path = tmpdir / "h4.csv"
            d1_path = tmpdir / "d1.csv"
            w1_path = tmpdir / "w1.csv"
            save_slice(h4, window_start, test_end, h4_path)
            save_slice(d1, window_start, test_end, d1_path)
            save_slice(w1, window_start, test_end, w1_path)

            trades = engine.run(h4_path, d1_path, w1_path, args.news)
            if trades is None or trades.empty:
                rows.append({
                    "train_start": window_start,
                    "train_end": train_end,
                    "test_end": test_end,
                    "trades": 0,
                    "net_pnl": 0.0,
                    "profit_factor": 0.0,
                    "win_rate": 0.0,
                })
            else:
                test_trades = trades[
                    pd.to_datetime(trades["entry_time"], utc=True) >= train_end
                ].copy()
                gp = test_trades.loc[test_trades.pnl > 0, "pnl"].sum()
                gl = -test_trades.loc[test_trades.pnl < 0, "pnl"].sum()
                rows.append({
                    "train_start": window_start,
                    "train_end": train_end,
                    "test_end": test_end,
                    "trades": len(test_trades),
                    "net_pnl": test_trades.pnl.sum(),
                    "profit_factor": gp / gl if gl else float("inf"),
                    "win_rate": (test_trades.pnl > 0).mean() if len(test_trades) else 0.0,
                })

            window_start = window_start + test

    result = pd.DataFrame(rows)
    result.to_csv(args.out, index=False)
    print(result.to_string(index=False))
    print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    main()
