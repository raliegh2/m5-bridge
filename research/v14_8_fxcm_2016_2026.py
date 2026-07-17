"""Run the frozen V14.8 replay against the independent FXCM H1 archive."""
from __future__ import annotations

import json

import pandas as pd

from research import v14_8_dukascopy_2016_2026_backtest as study

LATEST_COMMON_CANDLE = pd.Timestamp("2026-05-01T20:00:00Z")


if __name__ == "__main__":
    study.DATA = study.ROOT / "research" / "fxcm_2016_2026_data"
    study.TEST_END = LATEST_COMMON_CANDLE
    study.main()
    path = study.OUT / "v14_8_dukascopy_results.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["provider"] = "FXCM official weekly H1 candle archive"
    payload["source_manifest"] = str(
        study.ROOT / "research" / "fxcm_2016_2026_data" / "manifest.json"
    )
    payload["latest_common_source_candle"] = LATEST_COMMON_CANDLE.isoformat()
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    report_path = study.OUT / "BACKTEST_REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace("Dukascopy", "FXCM")
    report += (
        "\n## External feed\n\n"
        "The H1 source files are FXCM's official weekly candle archive. Each file contains "
        "bid and ask OHLC values with UTC timestamps. H4 and D1 were rebuilt from the H1 bid series. "
        "The latest completed candle common to all five downloaded symbols is 2026-05-01 20:00 UTC.\n"
    )
    report_path.write_text(report, encoding="utf-8")
