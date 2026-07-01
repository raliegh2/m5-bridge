"""Historical data loading for backtests.

Two sources:
- ``load_csv`` reads an OHLC CSV (e.g. exported from MetaTrader 5).
- ``fetch_history`` pulls bars from a live MT5 client (Windows only).

Both return a DataFrame with at least: time, open, high, low, close.
"""

import pandas as pd

_COLUMN_ALIASES = {
    "date": "time", "datetime": "time", "timestamp": "time", "<DATE>": "time",
    "o": "open", "h": "high", "l": "low", "c": "close",
    "<OPEN>": "open", "<HIGH>": "high", "<LOW>": "low", "<CLOSE>": "close",
}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={c: _COLUMN_ALIASES.get(c, c) for c in df.columns})
    df = df.rename(columns={c: c.lower() for c in df.columns})
    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")
    if "time" not in df.columns:
        df["time"] = range(len(df))
    return df[["time", "open", "high", "low", "close"]].reset_index(drop=True)


def load_csv(path: str) -> pd.DataFrame:
    """Load OHLC data from a CSV. Tolerant of common column-name variants."""
    df = pd.read_csv(path)
    return _normalise(df)


def fetch_history(client, symbol: str, timeframe: str = "M30",
                  bars: int = 5000) -> pd.DataFrame:
    """Fetch bars from a live MT5 client (Windows). Returns OHLC DataFrame."""
    rates = client.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No history returned for {symbol} {timeframe}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return _normalise(df)
