# V13 V12 Final + V11 Intraday Research Branch

This branch adds the V11 intraday/day-trading system to the V12 Final supervised
research profile.

## Architecture

```text
V12 Final five-symbol strategy
        +
V11 intraday-only signal engines
        ↓
V13 combined research profile
        ↓
V12 Final-style risk controls
        ↓
manual/supervised proposal boundary
```

## Added V11 intraday engines

| V13 engine name | Source V11 engine | Symbol | Risk tiers |
|---|---|---|---|
| `GBPUSD_V11_INTRADAY` | `GBPUSD_SATELLITE_V3` | GBPUSD | 0.30%, 0.35%, 0.40% |
| `EURUSD_V11_INTRADAY` | `EURUSD_SATELLITE_V7` | EURUSD | 0.30%, 0.35%, 0.40% |
| `GBPJPY_V11_INTRADAY` | `GBPJPY_SATELLITE_V7` | GBPJPY | 0.25%, 0.35%, 0.40% |

## Safety posture

- Research only.
- READ_ONLY.
- Broker order API disabled.
- Human review required.
- V11 side remains intraday-only.
- V11 side does not allow overnight positions.
- V11 side force-flat hour: 20:00 UTC.

## Available-data estimate

The available-data estimate is documented in:

```text
research/V13_V12_FINAL_PLUS_V11_INTRADAY_BACKTEST_REPORT.md
research/v13_v12_plus_v11_intraday_backtest_results.json
```

Headline result:

| Scenario | Profit |
|---|---:|
| V12 Final maximum-history | $3,201.58 |
| V13 additive estimate | $4,248.47 |
| Increase | +$1,046.89 / +32.70% |

This is not a raw execution backtest. A real combined test requires merged V12
and V11 candidate ledgers replayed chronologically through the V13 risk governor.
