"""ICT-style M5 trend / M1 entry research backtest harness.

Research only. No live trading. This script expects the uploaded MT5 CSV files
under research/data/ and writes results under research/v13_ict_m5_m1_out/.

Concept mapping:
- M5 higher-timeframe bias: EMA/MACD/RSI regime.
- M1 liquidity sweep: sweep prior rolling high/low and close back inside.
- Displacement/FVG: large candle body and optional three-candle imbalance.
- Order-flow proxy: tick-volume z-score, signed-volume z-score, close location.
- Entry: next M1 open after signal candle.

The script is intentionally conservative: it will not promote a setup unless
train + confirmation pass before out-of-sample testing.
"""
from __future__ import annotations

from pathlib import Path
import json
import zipfile
import gc

import numpy as np
import pandas as pd

DATA = Path("research/data")
OUT = Path("research/v13_ict_m5_m1_out")
OUT.mkdir(parents=True, exist_ok=True)

START = pd.Timestamp("2016-07-03")
TRAIN_END = pd.Timestamp("2021-12-31 23:59:59")
CONF_END = pd.Timestamp("2022-12-31 23:59:59")
TEST_START = pd.Timestamp("2023-01-01")
END = pd.Timestamp("2026-07-03 23:59:59")
START_BALANCE = 5000.0
RISK_PER_TRADE = 0.0035

SYMBOLS = {
    "GBPUSD": ("GBPUSD_M1_201601040000_202607031748(1).csv", "GBPUSD_M5_201601040000_202607031745(1).csv", 0.0001),
    "GBPJPY": ("GBPJPY_M1_201601040000_202607031748(1).csv", "GBPJPY_M5_201601040000_202607031745(1).csv", 0.01),
    "EURUSD": ("EURUSD_M1_201601040000_202607031745(1).csv", "EURUSD_M5_201601040000_202607031745(1).csv", 0.0001),
}


def read_mt5_csv(path: Path) -> pd.DataFrame:
    usecols = ["<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>", "<TICKVOL>", "<SPREAD>"]
    df = pd.read_csv(path, sep="\t", usecols=usecols)
    df.columns = ["date", "clock", "open", "high", "low", "close", "tickvol", "spread"]
    df["time"] = pd.to_datetime(df["date"] + " " + df["clock"], format="%Y.%m.%d %H:%M:%S")
    return df.drop(columns=["date", "clock"]).sort_values("time").reset_index(drop=True)


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / (down + 1e-12))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df.close.shift(1)
    true_range = pd.concat(
        [(df.high - df.low).abs(), (df.high - prev_close).abs(), (df.low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / n, adjust=False).mean()


def macd_hist(close: pd.Series) -> pd.Series:
    macd = ema(close, 12) - ema(close, 26)
    return macd - ema(macd, 9)


def prepare_symbol(symbol: str, m1_file: str, m5_file: str, pip: float):
    m1 = read_mt5_csv(DATA / m1_file)
    median_step = m1.time.diff().dropna().dt.total_seconds().median() / 60
    if median_step != 1.0:
        return None, {
            "freq": float(median_step),
            "rows": int(len(m1)),
            "start": str(m1.time.iloc[0]),
            "end": str(m1.time.iloc[-1]),
        }

    m5 = read_mt5_csv(DATA / m5_file)
    m1 = m1[(m1.time >= START - pd.Timedelta(days=30)) & (m1.time <= END)].reset_index(drop=True)
    m5 = m5[(m5.time >= START - pd.Timedelta(days=90)) & (m5.time <= END)].reset_index(drop=True)

    m1["ema9"] = ema(m1.close, 9)
    m1["ema20"] = ema(m1.close, 20)
    m1["rsi"] = rsi(m1.close, 14)
    m1["atrp"] = atr(m1, 14) / pip
    m1["spreadp"] = m1.spread / 10.0
    m1["bodyp"] = (m1.close - m1.open).abs() / pip
    m1["body_ma"] = m1.bodyp.rolling(20).mean()
    m1["cloc"] = ((m1.close - m1.low) / (m1.high - m1.low + 1e-12)).clip(0, 1)
    signed_volume = np.sign(m1.close - m1.open) * m1.tickvol
    m1["tick_z"] = (m1.tickvol - m1.tickvol.rolling(50).mean()) / (m1.tickvol.rolling(50).std() + 1e-9)
    m1["signed_z"] = (signed_volume - signed_volume.rolling(50).mean()) / (signed_volume.rolling(50).std() + 1e-9)
    m1["bull_fvg"] = m1.low > m1.high.shift(2)
    m1["bear_fvg"] = m1.high < m1.low.shift(2)
    for lookback in (15, 30, 60):
        m1[f"lo{lookback}"] = m1.low.shift(1).rolling(lookback).min()
        m1[f"hi{lookback}"] = m1.high.shift(1).rolling(lookback).max()

    m5["ema20"] = ema(m5.close, 20)
    m5["ema100"] = ema(m5.close, 100)
    m5["rsi5"] = rsi(m5.close, 14)
    m5["macdh5"] = macd_hist(m5.close)
    feats = m5[["time", "ema20", "ema100", "rsi5", "macdh5"]].copy()
    feats.columns = ["m5_time", "m5ema20", "m5ema100", "m5rsi", "m5macdh"]

    m1["m5_time"] = m1.time.dt.floor("5min") - pd.Timedelta(minutes=5)
    df = m1.merge(feats, on="m5_time", how="left")
    df = df[(df.time >= START) & (df.time <= END)].reset_index(drop=True)
    df["hour"] = df.time.dt.hour.astype(np.int16)
    df["dow"] = df.time.dt.dayofweek.astype(np.int16)
    return df, {
        "freq": float(median_step),
        "rows": int(len(df)),
        "start": str(df.time.iloc[0]),
        "end": str(df.time.iloc[-1]),
    }


def signal_and_r(df: pd.DataFrame, params: dict, pip: float, mask: np.ndarray):
    session = params["session"]
    if session == "london":
        sess = (df.hour >= 7) & (df.hour <= 10)
    elif session == "ny":
        sess = (df.hour >= 12) & (df.hour <= 16)
    else:
        sess = ((df.hour >= 7) & (df.hour <= 10)) | ((df.hour >= 12) & (df.hour <= 16))
    sess = sess & (df.dow < 5)

    bias_up = (df.m5ema20 > df.m5ema100) & (df.m5macdh > 0) & (df.m5rsi >= 50)
    bias_dn = (df.m5ema20 < df.m5ema100) & (df.m5macdh < 0) & (df.m5rsi <= 50)

    lb = int(params["lookback"])
    lo = df[f"lo{lb}"]
    hi = df[f"hi{lb}"]
    sweep_long = (df.low <= lo - 0.02 * df.atrp * pip) & (df.close > lo)
    sweep_short = (df.high >= hi + 0.02 * df.atrp * pip) & (df.close < hi)

    of_long = (df.tick_z >= -0.35) & (df.signed_z >= -0.25) & (df.cloc >= 0.52) & (df.close > df.open)
    of_short = (df.tick_z >= -0.35) & (df.signed_z <= 0.25) & (df.cloc <= 0.48) & (df.close < df.open)
    rsi_long = (df.rsi.shift(1) <= 45) & (df.rsi >= 47)
    rsi_short = (df.rsi.shift(1) >= 55) & (df.rsi <= 53)
    ema_long = (df.low.shift(1) <= df.ema20.shift(1)) & (df.close > df.ema9)
    ema_short = (df.high.shift(1) >= df.ema20.shift(1)) & (df.close < df.ema9)
    displacement_long = (df.close > df.open) & (df.bodyp >= 0.5 * df.body_ma) & (df.cloc >= 0.52)
    displacement_short = (df.close < df.open) & (df.bodyp >= 0.5 * df.body_ma) & (df.cloc <= 0.48)

    trigger = params["trigger"]
    if trigger == "sweep_rsi":
        long = sweep_long & (rsi_long | ema_long) & of_long
        short = sweep_short & (rsi_short | ema_short) & of_short
    elif trigger == "displacement_fvg":
        long = sweep_long & displacement_long & df.bull_fvg & (of_long | rsi_long)
        short = sweep_short & displacement_short & df.bear_fvg & (of_short | rsi_short)
    else:
        long = sweep_long & (rsi_long | ema_long | of_long) & (df.close > df.open)
        short = sweep_short & (rsi_short | ema_short | of_short) & (df.close < df.open)

    ok = sess & (df.spreadp <= 4.0) & (df.atrp >= 0.45)
    direction = np.zeros(len(df), dtype=np.int8)
    direction[(long & ok & bias_up).values] = 1
    direction[(short & ok & bias_dn).values] = -1
    if params.get("invert", False):
        direction = -direction

    hold = int(params["hold"])
    idx = np.flatnonzero((direction != 0) & mask)
    idx = idx[idx + hold + 1 < len(df)]
    if len(idx) == 0:
        return np.array([]), idx, direction

    entry = df.open.values[idx + 1] / pip
    exit_price = df.close.values[idx + hold] / pip
    stop = np.maximum(float(params["sl_atr"]) * df.atrp.values[idx], 2.5)
    r_mult = direction[idx] * (exit_price - entry) / stop - df.spreadp.values[idx] / stop
    r_mult = np.clip(r_mult, -1.0, float(params["rr"]))
    return r_mult, idx, direction


def metric_from_r(r: np.ndarray) -> dict:
    if len(r) == 0:
        return {"trades": 0, "total_r": 0.0, "pf": 0.0, "win_rate": 0.0, "avg_r": 0.0}
    gross_win = r[r > 0].sum()
    gross_loss = -r[r <= 0].sum()
    pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    return {"trades": int(len(r)), "total_r": float(r.sum()), "pf": float(pf), "win_rate": float((r > 0).mean()), "avg_r": float(r.mean())}


def main() -> int:
    params = []
    for trigger in ("sweep_rsi", "displacement_fvg", "lax_liquidity"):
        for lookback in (30, 60):
            for rr in (1.5, 2.0):
                for invert in (False, True):
                    params.append({"trigger": trigger, "lookback": lookback, "rr": rr, "sl_atr": 0.7, "hold": 60, "session": "both", "invert": invert})

    metadata = {}
    ranks = []
    selected = []
    trades = []

    for symbol, (m1_file, m5_file, pip) in SYMBOLS.items():
        df, meta = prepare_symbol(symbol, m1_file, m5_file, pip)
        metadata[symbol] = meta
        if df is None:
            selected.append({"symbol": symbol, "selected": False, "reason": "not true M1"})
            continue

        train_mask = ((df.time >= START) & (df.time <= TRAIN_END)).values
        confirm_mask = ((df.time > TRAIN_END) & (df.time <= CONF_END)).values
        test_mask = ((df.time >= TEST_START) & (df.time <= END)).values

        rows = []
        for k, params_i in enumerate(params):
            train_r, _, _ = signal_and_r(df, params_i, pip, train_mask)
            if len(train_r) < 20:
                continue
            confirm_r, _, _ = signal_and_r(df, params_i, pip, confirm_mask)
            train = metric_from_r(train_r)
            confirm = metric_from_r(confirm_r)
            combined_r = train["total_r"] + confirm["total_r"]
            eligible = (
                train["trades"] + confirm["trades"] >= 50
                and combined_r > -2.0
                and train["pf"] >= 0.90
                and confirm["pf"] >= 0.85
            )
            rows.append({"symbol": symbol, "k": k, "eligible": eligible, "score": combined_r + (train["pf"] - 1) * 5 + (confirm["pf"] - 1) * 5, **params_i,
                         "train_r": train["total_r"], "train_pf": train["pf"], "train_trades": train["trades"],
                         "confirm_r": confirm["total_r"], "confirm_pf": confirm["pf"], "confirm_trades": confirm["trades"]})

        rank = pd.DataFrame(rows).sort_values(["eligible", "score"], ascending=[False, False]) if rows else pd.DataFrame()
        rank.to_csv(OUT / f"{symbol}_ict_rank.csv", index=False)
        if not rank.empty:
            ranks.append(rank)
        eligible_rows = rank[rank.eligible].head(1) if not rank.empty else pd.DataFrame()
        if eligible_rows.empty:
            best = rank.head(1).to_dict("records")[0] if not rank.empty else {}
            selected.append({"symbol": symbol, "selected": False, "reason": "no ICT setup passed lax train/confirmation gate", **{f"best_{k}": v for k, v in best.items() if k in ("score", "train_r", "train_pf", "train_trades", "confirm_r", "confirm_pf", "confirm_trades")}})
        else:
            selected_row = eligible_rows.iloc[0].to_dict()
            params_i = {key: selected_row[key] for key in params[0]}
            test_r, idx, direction = signal_and_r(df, params_i, pip, test_mask)
            test = metric_from_r(test_r)
            selected.append({"symbol": symbol, "selected": True, **selected_row, **{f"test_{k}": v for k, v in test.items()}})
            if len(test_r):
                hold = int(params_i["hold"])
                trades.append(pd.DataFrame({"entry_time": df.time.values[idx + 1], "exit_time": df.time.values[idx + hold], "r": test_r, "direction": direction[idx], "symbol": symbol}))
        del df
        gc.collect()

    selected_df = pd.DataFrame(selected)
    selected_df.to_csv(OUT / "selected.csv", index=False)
    if ranks:
        pd.concat(ranks, ignore_index=True).to_csv(OUT / "all_ict_rank.csv", index=False)
    else:
        pd.DataFrame().to_csv(OUT / "all_ict_rank.csv", index=False)

    trades_df = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(columns=["entry_time", "exit_time", "r", "direction", "symbol"])
    trades_df.to_csv(OUT / "ict_oos_trades.csv", index=False)

    balance = START_BALANCE
    peak = balance
    wins = losses = 0
    gross_win = gross_loss = 0.0
    equity_rows = []
    max_dd = 0.0
    if not trades_df.empty:
        trades_df = trades_df.sort_values("entry_time")
        for _, row in trades_df.iterrows():
            pnl = balance * RISK_PER_TRADE * row.r
            balance += pnl
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak * 100)
            equity_rows.append({"time": row.exit_time, "equity": balance, "pnl": pnl, "r": row.r, "symbol": row.symbol})
            if pnl > 0:
                wins += 1
                gross_win += pnl
            else:
                losses += 1
                gross_loss += -pnl
    pd.DataFrame(equity_rows).to_csv(OUT / "equity_curve.csv", index=False)
    pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    summary = {
        "starting_balance": START_BALANCE,
        "ending_balance": balance,
        "net_profit": balance - START_BALANCE,
        "return_percent": (balance - START_BALANCE) / START_BALANCE * 100,
        "trades": int(len(trades_df)),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate": wins / len(trades_df) if len(trades_df) else 0.0,
        "profit_factor": pf,
        "max_drawdown_percent": max_dd,
        "risk_per_trade_percent": RISK_PER_TRADE * 100,
    }

    with open(OUT / "summary.json", "w", encoding="utf-8") as handle:
        json.dump({"metadata": metadata, "summary": summary, "selected": selected}, handle, indent=2, default=str)

    report = [
        "# V13 ICT-Style M5/M1 Backtest Report",
        "",
        "Status: research-only fixed-hold no-lookahead validation",
        "",
        "## Portfolio result",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in summary.items():
        report.append(f"| {key} | {value:.4f} |" if isinstance(value, float) else f"| {key} | {value} |")
    report += ["", "## Selected setups", selected_df.to_markdown(index=False), "", "## Decision", "Selected only if train+confirmation passed the lax gate without using test results."]
    (OUT / "V13_ICT_M5_M1_BACKTEST_REPORT.md").write_text("\n".join(report), encoding="utf-8")

    with zipfile.ZipFile(OUT / "v13_ict_m5_m1_outputs.zip", "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in OUT.iterdir():
            if file.is_file():
                zipf.write(file, file.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
