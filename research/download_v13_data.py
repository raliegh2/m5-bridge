"""Download research OHLC files used by V13 and save normalized CSV copies."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v13_data"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://raw.githubusercontent.com/ejtraderLabs/historical-data/main"

for symbol in ("AUDUSD", "USDCAD", "NZDUSD", "USDJPY"):
    for timeframe in ("h1", "h4", "d1"):
        url = f"{BASE}/{symbol}/{symbol}{timeframe}.csv"
        frame = pd.read_csv(url)
        frame.to_csv(OUT / f"{symbol}_{timeframe}.csv", index=False)
        print(symbol, timeframe, len(frame))
