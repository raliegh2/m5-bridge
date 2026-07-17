"""Run the V14.8 external replay and record the documented Dukascopy API source."""
from __future__ import annotations

import json

from research.v14_8_dukascopy_2016_2026_backtest import OUT, main as run_backtest


if __name__ == "__main__":
    run_backtest()
    path = OUT / "v14_8_dukascopy_results.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["provider"] = "Dukascopy Trading Tools API"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
