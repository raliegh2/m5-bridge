# V14.22 Range Breakout-Retest Research Arm

V14.22 investigates a new way to use the range detector without reactivating the failed V14.19 mean-reversion orders.

## Hypothesis

A completed D1 range is treated as compression rather than as an instruction to fade an extreme. A later completed H4 candle must displace beyond the frozen 20-day range boundary, and a subsequent H4 candle must retest and hold that boundary. Entry occurs only on the next H4 open.

## Pre-registered profiles

- `BALANCED_2R`: moderate displacement and retest requirements, 2.0R target.
- `CONSERVATIVE_2_5R`: stronger displacement, tighter retest tolerance and 2.5R target.
- `FAST_1_5R`: shorter breakout/retest window and 1.5R target.

The profiles are fixed before the official-data replay. Selection uses only retail-cost results from 2016-2020. Data from 2021-2026 is reserved for audit and forward evidence.

## Data and execution assumptions

- Official FXCM H1 bid and ask archives.
- Completed D1 range state and completed H4 breakout/retest candles.
- Long entry at ask and exit at bid; short entry at bid and exit at ask.
- Broker spread is embedded directly.
- A 0.025R base execution reserve plus the inherited scenario reserve is deducted.
- Stop-first ordering is used when stop and target occur in one bar.
- One shadow trade per symbol at a time.

## Safety boundary

- The V14.19 mean-reversion family remains disabled.
- All V14.22 trades request and execute 0.00% risk.
- No MetaTrader import or broker transmission exists in the research module.
- A passing historical gate does not authorize demo or funded execution. A separate demo-forward integration would be required.
