# Active Satellites V2

## Revised architecture

- **GBPUSD V4 Swing** remains the primary low-frequency engine. Its frozen D1/H4 rules, ATR stops, partial exit, break-even, trailing logic and 3-12 day holding profile are unchanged.
- **EURUSD Satellite V2** is a higher-frequency D1/H4 model using two independent setups: EMA-cluster pullback continuation and session-range breakout continuation.
- **GBPJPY Satellite V2** is a higher-frequency D1/H4 model using volatility-expansion breakouts and strong-trend pullback continuation.
- Satellites target at least three trades per week on average during research, but no live quota forces a trade when conditions are absent.

## Portfolio risk

- hard maximum risk per trade: 0.50%
- default GBPUSD risk: 0.35%
- default EURUSD risk: 0.20%
- default GBPJPY risk: 0.15%
- maximum aggregate initial open risk: 0.75%
- maximum newly committed risk per day: 0.75%
- maximum newly committed risk per week: 2.00%
- maximum three open positions, one per symbol
- combined GBPUSD and GBPJPY risk cap: 0.50%
- daily loss stop: 2.0% or stricter account-specific limit
- weekly loss stop: 4.0%
- drawdown throttle at 6%; complete pause at 10%

## EURUSD satellite rules

### Bias

- D1 EMA50 above EMA200 for long bias; below for short bias
- D1 ADX >= 16
- H4 EMA21/EMA50 aligned with D1 bias
- H4 ATR percentile between 20% and 90%

### Setup A: quick pullback

- H4 touches or penetrates the EMA21/EMA50 zone
- candle closes back with the bias
- RSI 45-68 for longs or 32-55 for shorts
- body/range >= 0.25
- entry on next completed H4 bar

### Setup B: session-range continuation

- London/NY H4 close breaks the prior six H4-bar extreme
- D1 bias and H4 EMA alignment agree
- RSI confirms but is not extreme
- body/range >= 0.40

### Management

- stop 0.9-1.1 ATR, pair-specific by setup
- 50% scale-out at 1.5R
- break-even after scale-out
- trail remainder by 1.6 ATR
- maximum hold 30 H4 bars (five trading days)

## GBPJPY satellite rules

### Bias

- D1 EMA50/EMA100 trend alignment
- D1 ADX >= 20
- D1 ATR percentile >= 40%
- H4 Bollinger width or ATR expanding

### Setup A: volatility breakout

- H4 close breaks prior eight-bar high/low
- momentum body/range >= 0.45
- RSI >= 56 long or <= 44 short
- London-session preference

### Setup B: strong-trend pullback

- H4 ADX >= 24
- pullback toward EMA21/EMA50
- resumption candle closes in trend direction
- no entry after an oversized candle above 2.2 ATR

### Management

- stop 1.4-1.7 ATR
- 40% scale-out at 1.8R
- aggressive trail by 2.0 ATR after partial
- maximum hold 42 H4 bars (seven trading days)

## Validation gates

- minimum average satellite frequency: 3 trades/week over full OOS period
- OOS PF >= 1.25 per satellite and >= 1.40 combined satellites
- positive expectancy after 25% spread/slippage stress
- maximum satellite drawdown below 8%
- combined portfolio drawdown below 10-12%
- at least four profitable rolling test windows out of five
- no single year contributes more than 50% of total profit
- forward demo cohort of at least 30 completed trades per satellite

The frequency target is a validation metric, not a live-entry quota.
