# GBPUSD Satellite V2 preliminary results

## Objective

Satellite V2 was designed around these operating targets:

- 5-15 trades per week
- $2-$5 average profit per completed trade on a $5,000 account
- profit factor of approximately 1.40-1.60 or better
- no more than 0.50% new risk committed per UTC day
- no more than 0.50% combined open GBPUSD risk

The strategy never forces a trade quota. A quiet week is preferable to inserting
low-quality entries merely to reach five trades.

## Implemented setups

### London pullback

- H1 EMA20/EMA50 trend with ADX at least 10
- matching M30 EMA20/EMA50 trend
- pullback during the previous five M15 candles
- M15 momentum resumption through the prior two-bar extreme
- body ratio at least 0.50
- volume ratio at least 0.80
- RSI at least 52 for longs or at most 48 for shorts
- 1.75 ATR stop, clipped to 5-30 pips
- 1.75R target
- break-even after 1R

### New York retest

- H1 EMA20/EMA50 trend with ADX at least 20
- completed London range from 07:00-12:00 UTC
- breakout during the previous six M15 candles
- M15 retest that closes back beyond the London range
- volume ratio at least 0.80
- RSI at least 54 for longs or at most 46 for shorts
- 1.25 ATR stop, clipped to 6-30 pips
- 3R target
- break-even after 1.5R

## Execution and risk assumptions

- Starting balance: $5,000
- Requested base size: 0.08 lots
- Size clipped so initial risk is no more than 0.25% per trade
- Maximum newly committed risk per UTC day: 0.50%
- One London and one New York entry maximum per day
- One satellite position at a time
- 0.8-pip minimum spread
- 0.3-pip slippage per execution side
- Conservative stop-first same-candle handling
- Forced flat by 20:00 UTC

## Available-data result

Data coverage after indicator warm-up:

- 2025-07-04 01:45 UTC through 2026-07-01 14:45 UTC

| Metric | Result |
|---|---:|
| Ending balance | $5,358.54 |
| Net profit | **+$358.54** |
| Return | **+7.17%** |
| Completed trades | 173 |
| Trades per week | **3.34** |
| Average profit per trade | **$2.07** |
| Profit factor | **1.43** |
| Win rate | 39.31% |
| Maximum mark-to-market drawdown | **2.24%** |

### Setup contribution

| Setup | Trades | Net profit | Average/trade | PF |
|---|---:|---:|---:|---:|
| London pullback | 132 | +$228.80 | $1.73 | 1.36 |
| New York retest | 41 | +$129.75 | $3.16 | 1.65 |

## Development and later validation split

| Period | Trades | Net | Average/trade | PF | Max DD |
|---|---:|---:|---:|---:|---:|
| 2025-07-15 to 2026-01-01 | 82 | +$239.15 | $2.92 | 1.64 | 1.24% |
| 2026-01-01 to 2026-07-01 | 89 | +$93.22 | $1.05 | 1.21 | 2.24% |

During the later validation segment, the New York setup produced -$2.20 with a
PF of approximately 0.98. The London setup remained positive but achieved only
PF 1.29. This is material evidence that the apparent full-sample edge weakened.

## Target assessment

| Target | Result | Status |
|---|---:|---|
| 5-15 trades/week | 3.34 | **Not met** |
| $2-$5 average/trade | $2.07 full sample | Met full sample; not later validation |
| PF 1.40-1.60+ | 1.43 full sample | Met full sample; not later validation |
| Daily new risk under 0.5-1.0% | 0.50% hard cap | Met by construction |

## Decision

Satellite V2 is an improvement over the original 1.20-PF satellite on the
available one-year sample, but it is not production validated. It should remain
on a research branch and run only in READ_ONLY or approval/demo mode.

Do not merge Satellite V2 to main for unattended live execution until:

1. ten or more years of broker M15 data are exported;
2. 10/5/3/2-year windows are completed;
3. rolling walk-forward tests remain profitable;
4. the later-period PF improves above the pre-registered threshold;
5. 20-30 genuinely forward trades reconcile with MT5 and the dashboard.
