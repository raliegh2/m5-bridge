# Strategy Engine V8

This branch adds a frozen four-engine research profile and a synchronized trade-ledger replay.

Engines:

- `EURUSD_SATELLITE_V7`: EURUSD, 0.25% risk.
- `GBPJPY_SATELLITE_V7`: GBPJPY, 0.25% risk.
- `GBPUSD_SATELLITE_V2`: GBPUSD, 0.25% risk.
- `GBPUSD_SWING_V6`: GBPUSD, 0.50% risk.

The GBPUSD swing engine uses M30 entry timing, M1/M5 observation, and H4/D1 trend anchors. Its frozen management contract uses a 1.5 ATR initial stop clipped to 20-150 pips, a 50% partial at 1R, break-even after the partial, a 3R final objective, 2.5 ATR trailing, and a maximum hold of 72 H4 bars.

Shared controls use a $5,000 research balance, three concurrent positions, a 0.75% total open-risk cap, a 0.75% aligned GBP cap, and a 0.50% mixed-direction GBP cap.

Replay command for an exported synchronized ledger:

```text
python -m mt5_ai_bridge.v8_backtest path/to/strategy_engine_v8_synchronized_trades.csv --rejected path/to/strategy_engine_v8_rejected_candidates.csv --output strategy_engine_v8_verified.json
```

The replay verifies portfolio coordination from accepted trade ledgers. It is not a tick-accurate OHLC simulation. The verified output from the supplied run is stored in `backtest_results/strategy_engine_v8_verified.json`.
