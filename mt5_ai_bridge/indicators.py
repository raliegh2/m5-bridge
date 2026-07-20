"""Technical indicators and market snapshot.

The indicator math (``ema``/``rsi``/``macd``/``atr``) is pure pandas and fully
unit testable. Only ``get_rates`` / ``market_snapshot`` touch the broker, via
the injected client.
"""

import pandas as pd


def get_rates(client, symbol: str, timeframe: str = "M30", bars: int = 200):
    rates = client.copy_rates_from_pos(symbol, timeframe, 0, bars)

    if rates is None or len(rates) == 0:
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (volatility, in price units)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def efficiency_ratio(series: pd.Series, period: int = 20) -> pd.Series:
    """Kaufman Efficiency Ratio series (~1 clean trend, ~0 chop)."""
    net = series.diff(period).abs()
    path = series.diff().abs().rolling(period).sum()
    return net / path


def add_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    df["ema_9"] = ema(df["close"], 9)
    df["ema_20"] = ema(df["close"], 20)
    df["ema_50"] = ema(df["close"], 50)
    df["ema_200"] = ema(df["close"], 200)

    df["rsi_14"] = rsi(df["close"], 14)
    df["er"] = efficiency_ratio(df["close"], 20)   # regime: directional vs range

    df["macd"], df["macd_signal"], df["macd_hist"] = macd(df["close"])

    if {"high", "low"}.issubset(df.columns):
        df["atr"] = atr(df, atr_period)
    return df


def market_snapshot(client, symbol: str, timeframe: str = "M30",
                    atr_period: int = 14) -> dict | None:
    df = get_rates(client, symbol, timeframe)

    if df is None:
        return None

    df = add_indicators(df, atr_period=atr_period)
    latest = df.iloc[-1]

    atr_val = latest["atr"] if "atr" in df.columns else None
    return {
        "symbol": symbol,
        "time": str(latest["time"]),
        "close": float(latest["close"]),
        "ema_9": float(latest["ema_9"]),
        "ema_20": float(latest["ema_20"]),
        "ema_50": float(latest["ema_50"]),
        "ema_200": float(latest["ema_200"]),
        "rsi_14": float(latest["rsi_14"]),
        "macd": float(latest["macd"]),
        "macd_signal": float(latest["macd_signal"]),
        "macd_hist": float(latest["macd_hist"]),
        "atr": (float(atr_val) if atr_val is not None and atr_val == atr_val else None),
        "er": (float(latest["er"]) if "er" in df.columns and latest["er"] == latest["er"] else None),
    }
