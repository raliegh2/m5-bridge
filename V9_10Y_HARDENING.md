# Strategy Engine V9 — Ten-Year Validation and Runtime Hardening

## Current verdict

An honest ten-year **portfolio** backtest is blocked until raw 2016–2026 M15
history is available for GBPUSD, EURUSD, and GBPJPY. The V9 hour gate changes a
GBPUSD M15 engine, so a ten-year swing ledger cannot substitute for the missing
intraday bars.

The repository already contains ten-year evidence for the GBPUSD swing family,
but the active satellite histories begin later. The new preflight command makes
it impossible to publish a partial-coverage run as a ten-year result.

## What this branch changes

1. `gbpusd_satellite_v3.py` wraps the tested V2 signal engine and applies the
   frozen V9 hours: 07, 10, 11, 12, 14, 15, and 16 UTC.
2. Signals are rejected when stale, during excessive spread, or around supplied
   high-impact events.
3. A short TTL cache avoids repeatedly downloading and recalculating the same
   MT5 history in fast polling loops.
4. `AtomicBarRegistry` provides idempotent completed-bar state with atomic disk
   writes.
5. `export_v9_history.py` exports the required raw data in yearly chunks and
   creates a SHA-256 coverage manifest.
6. `v9_10y_preflight.py` checks coverage, duplicates, invalid OHLC rows, and
   suspicious weekday gaps before simulation.
7. V9 remains `READ_ONLY` by default.

## Export the raw data

Run on the Windows/MT5 host:

```powershell
python tools/export_v9_history.py `
  --start 2016-07-01T00:00:00+00:00 `
  --end 2026-07-01T00:00:00+00:00 `
  --out data/v9_10y
```

## Run the strict coverage gate

```powershell
python research/v9_10y_preflight.py `
  --start 2016-07-01T00:00:00+00:00 `
  --end 2026-07-01T00:00:00+00:00 `
  --gbpusd-m15 data/v9_10y/GBPUSD_M15_2016-07-01_2026-07-01.csv `
  --gbpusd-h4 data/v9_10y/GBPUSD_H4_2016-07-01_2026-07-01.csv `
  --gbpusd-d1 data/v9_10y/GBPUSD_D1_2016-07-01_2026-07-01.csv `
  --eurusd-m15 data/v9_10y/EURUSD_M15_2016-07-01_2026-07-01.csv `
  --gbpjpy-m15 data/v9_10y/GBPJPY_M15_2016-07-01_2026-07-01.csv
```

Do not continue unless the output says `READY` and
`ten_year_label_allowed: true`.

## Enable the V9 route after validation

```powershell
python tools/apply_strategy_engine_v9.py
```

Then copy `.env.strategy-engine-v9.example` to the local environment and keep
`MODE=READ_ONLY` until transaction-cost stress, yearly walk-forward tests, and a
demo reconciliation pass.

## Required promotion gates

- Full raw-data coverage for every active engine.
- Completed-bar, no-look-ahead simulation.
- Actual or variable spread, slippage, and swap included.
- Conservative stop-first handling when stop and target occur in one bar.
- Purged rolling walk-forward results, reported year by year.
- Event-calendar stress with missing-event coverage explicitly disclosed.
- At least 30 demo trades with broker fills reconciled to expected fills.
- V8 remains the control version; V9 is not silently substituted.
