"""Download monthly short-term interest rates used by V14.11 macro carry.

The FRED graph CSV endpoint does not require an API key. Values are sourced from
OECD Main Economic Indicators through FRED. The backtest applies a 45-day
publication lag before a monthly observation becomes available to a signal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import requests

OUT = Path(os.environ.get("FRED_RATE_OUT", "research/fred_short_rates_2014_2026"))
START = os.environ.get("FRED_RATE_START", "2014-01-01")
END = os.environ.get("FRED_RATE_END", "2026-05-31")
SERIES = {
    "USD": "IRSTCI01USM156N",
    "GBP": "IRSTCI01GBM156N",
    "EUR": "IRSTCI01EZM156N",
    "AUD": "IRSTCI01AUM156N",
    "JPY": "IRSTCI01JPM156N",
}


def download(currency: str, series_id: str) -> dict[str, object]:
    url = (
        "https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id={series_id}&cosd={START}&coed={END}"
    )
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    path = OUT / f"{currency}_{series_id}.csv"
    path.write_bytes(response.content)
    frame = pd.read_csv(path)
    if frame.empty or len(frame.columns) != 2:
        raise RuntimeError(f"{currency}/{series_id}: invalid FRED CSV")
    date_column, value_column = frame.columns
    frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
    frame = frame.dropna().sort_values(date_column)
    if len(frame) < 100:
        raise RuntimeError(f"{currency}/{series_id}: only {len(frame)} observations")
    frame.columns = ["observation_date", "rate_percent"]
    frame.to_csv(path, index=False)
    return {
        "currency": currency,
        "series_id": series_id,
        "observations": int(len(frame)),
        "start": frame["observation_date"].min().date().isoformat(),
        "end": frame["observation_date"].max().date().isoformat(),
        "file": str(path),
        "source_url": url,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    downloads = [download(currency, series) for currency, series in SERIES.items()]
    manifest = {
        "provider": "FRED / OECD Main Economic Indicators",
        "publication_lag_days_used_by_backtest": 45,
        "downloads": downloads,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
