# V12-derived M15/M30 intraday research

This branch is separate from the final V12 model. It copies only the general
completed-bar, trend-alignment, fixed-risk, and fail-closed principles.

- Entry timeframe: completed M15 candle
- Confirmation timeframe: completed M30 candle
- Execution-resolution data: M5
- Instrument currently available: GBPUSD
- Per-trade research risk: 0.25%
- Daily loss gate: 1.0%
- One position at a time
- Conservative stop-first handling when stop and target occur in one M5 bar
- Fixed 1.0-pip spread plus 0.2-pip slippage on each side

Run:

```powershell
$env:PYTHONPATH="research"
.\.venv\Scripts\python.exe research\v12_intraday_m15_m30_backtest.py
```

The `$50/week` figure is an evaluation target, not a trading quota or promise.
The available M5 file covers only about eight months, so positive results would
still require substantially longer out-of-sample and forward-demo validation.
