# V14.19 Range Mean-Reversion Shadow Engine

V14.19 is stacked on `v14-18-hierarchical-regime-meta-labeler`.

## Scope

The validated V14.18 portfolio is replayed unchanged. V14.19 adds a separate
D1 range mean-reversion research stream that:

- uses the official FXCM H1 bid/ask archive;
- builds completed H4 and D1 candles;
- generates a signal only after a D1 candle closes;
- enters only on a later 08:00, 12:00 or 16:00 UTC H4 bar;
- models long entries at ask and exits at bid, and short entries at bid and
  exits at ask;
- applies conservative stop-first intrabar ordering;
- stores zero requested risk and zero executed risk;
- never calls MT5 or transmits a broker order.

## Frozen profile

- D1 ADX maximum: 25.
- Absolute 20-day z-score entry: 1.25.
- EMA20/EMA50 separation: no more than 0.80 D1 ATR.
- Prior 20-day width: no more than 12 D1 ATR.
- Close reclaim: at least 25% from the swept daily extreme.
- Stop: 2.20 D1 ATR.
- Target: 1.60R.
- Time exit: 15 H4 bars.
- Additional execution reserve: 0.025R plus the inherited scenario reserve.
- One open shadow trade per symbol.

## Promotion boundary

The report calculates an evidence gate using at least 100 closed trades,
positive retail and stress expectancy, a positive 2024-2026 block and
nonnegative chronological blocks. Passing that historical gate does not
authorize promotion. V14.19 remains shadow-only and requires later demo-forward
evidence before any separate promotion proposal.

No merge, deployment, AUTO execution or funded-account authorization is added.
