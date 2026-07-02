# Multi-Pair Swing System V1

## Status

This branch is a research and demo implementation. GBPUSD V4 remains the only
validated core. EURUSD and GBPJPY models remain disabled until their independent
walk-forward gates pass.

## Portfolio architecture

The system is organized into five layers:

1. **Data and feature layer**
   - H1 source data resampled into H4, D1 and W1
   - ATR, ADX, RSI, EMA, Bollinger bandwidth and rolling percentile features
   - prior-day range and prior-day close location
   - weekly trend slope
   - overnight range and daily opening gap
   - spread-to-ATR ratio
   - scheduled-event classification loaded from CSV

2. **Pair model layer**
   - GBPUSD: frozen V4 D1/H4 trend-expansion engine
   - EURUSD: D1 trend plus H4 EMA-zone pullback model
   - GBPJPY: D1 momentum regime plus H4 breakout/deep-pullback hybrid

3. **Regime gate layer**
   - pair-specific volatility percentile ranges
   - weekly trend agreement
   - ADX threshold
   - event-day exclusion or risk reduction
   - gap and overnight-range exclusion
   - independent long/short enable states

4. **Portfolio risk layer**
   - maximum 0.50% risk per trade
   - default research risk: GBPUSD 0.35%, EURUSD 0.25%, GBPJPY 0.20%
   - maximum 0.75% aggregate initial risk across all open positions
   - maximum three total open positions, one per symbol
   - correlated GBP exposure cap for GBPUSD and GBPJPY
   - $250 daily loss limit and $500 total-loss limit on a $5,000 account
   - portfolio drawdown throttle at 3% and full pause at 6%

5. **Execution and monitoring layer**
   - completed-candle evaluation only
   - broker-visible stop and target
   - partial exit at 1R to 1.5R
   - break-even after partial exit
   - ATR trailing stop
   - maximum hold of 72 H4 bars, approximately 12 trading days
   - dashboard attribution by symbol, engine and setup

## Shared swing rules

- Intended holding period: three to twelve trading days
- Daily trend filter and H4 entry
- ATR-based stop and position size
- Realistic spread, slippage and swap assumptions
- No entry when event data is required but unavailable
- No parameter change inside a registered forward-test cohort

## EURUSD model

### Long regime

- D1 close above EMA200
- D1 ADX above the selected threshold
- W1 EMA20 slope positive
- H4 ATR percentile inside the approved middle-volatility band
- prior-day close in the upper part of the prior-day range
- daily gap below the pair-specific ATR threshold
- overnight range below the pair-specific ATR threshold
- spread-to-H4-ATR ratio below the configured ceiling

### Long trigger

- H4 low enters the EMA21/EMA50 pullback zone
- H4 closes back above EMA21
- bullish pin or engulfing body
- RSI remains inside the long continuation band

Short rules are independently parameterized and symmetrical only where the data
supports symmetry. Long and short states can be disabled separately.

Default EURUSD stop research range: 1.0 to 1.2 ATR. Default target research
range: 2R to 3R. Partial exit: 50% at 1R.

## GBPJPY model

### Long regime

- D1 EMA100 above EMA200
- W1 EMA20 slope positive
- D1 ADX above the selected threshold
- D1 ATR percentile and Bollinger bandwidth expanding
- gap, overnight range and spread-to-ATR states acceptable

### Long trigger

Either:

- H4 close breaks the prior swing high with a momentum body; or
- H4 makes a deep pullback toward EMA50 and closes back above EMA21.

Short rules use separate thresholds and can be disabled independently.

Default GBPJPY stop research range: 1.5 to 2.0 ATR. Default target research
range: 2R to 3R. Partial exit: 40% to 50% at 1R to 1.5R.

## Scheduled-event classification

The research runner accepts an event CSV with:

```text
symbol,event_time_utc,impact,event_type
EURUSD,2026-07-30T12:15:00Z,HIGH,ECB
GBPJPY,2026-08-07T03:00:00Z,HIGH,BOJ
```

High-impact events may disable entries for the entire trading day or reduce risk.
A missing event calendar is treated as incomplete validation, not as permission
to trade through events.

## Walk-forward protocol

- four years training
- one year untouched test
- 15-business-day purge on both sides of every boundary
- parameter selection based only on the training window
- every test window begins with a fresh $5,000 balance
- minimum test trades per pair before promotion
- results reported separately for long and short directions

Promotion gates:

- aggregate OOS PF at least 1.30
- positive aggregate OOS expectancy
- at least four of five OOS windows profitable
- OOS maximum drawdown below 5%
- no single test window responsible for most total profit
- cost-stressed PF above 1.10
- stable long and short behavior or explicit disabling of the weaker side

## Production gates

EURUSD and GBPJPY remain disabled until:

- complete walk-forward outputs are committed
- event calendar coverage is documented
- local tests pass
- 20 to 30 forward demo trades per enabled pair reconcile with MT5
- dashboard and broker records match

The system must never be promoted simply because an in-sample parameter set has
a high profit factor.
