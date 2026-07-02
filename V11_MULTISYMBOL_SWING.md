# V11 Multi-Symbol Swing

V11 keeps the GBPUSD, EURUSD and GBPJPY satellite strategies but replaces the
legacy GBPUSD swing entry with a common multi-symbol swing layer.

## Entries

- Core: completed D1 trend, completed H4 ADX, and a close through the prior
  55 H4-bar range.
- GBPUSD retest: completed H4 retest after a confirmed H4 breakout.
- EURUSD / GBPJPY retest: completed H1 retest after a confirmed H4 breakout.

## Management

- Core: 1.25 ATR stop, 3R target, 2.5 ATR trail after 1R, 24 H4-bar limit.
- GBPUSD retest: 1.5 ATR stop, 4R target, 2 ATR trail after 1R, 36 H4-bar limit.
- EURUSD / GBPJPY retest: 1.25 ATR stop, 3R target, 1.5 ATR trail after 1R,
  96 H1-bar limit.
- The manager supports setup-specific trailing and restart reconciliation.
- Partial closing is intentionally disabled in the frozen profile because the
  trailing-only profile produced better tested expectancy.

## Local run

Keep the existing `.env`, set `MODE=READ_ONLY`, and use the standalone entrypoint:

```powershell
python bridge_v11.py
```

The runner reuses the existing MT5 connection, dashboard, journal, reconnect
loop and V10 account-level risk controls without modifying `app.py`.
