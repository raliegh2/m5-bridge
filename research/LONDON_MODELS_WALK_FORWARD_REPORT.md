# Independent EURUSD and GBPJPY London-model research

## Objective

Create separate London-session models for EURUSD and GBPJPY rather than copying
GBPUSD parameters across symbols.

## Data limits

The supplied M15 histories cover only about four years:

- EURUSD M15: 2022-06-22 through 2026-07-01
- GBPJPY M15: 2022-06-06 through 2026-07-01

Longer H1/M30 histories were used only for indicator warm-up. Because entries
are generated from M15 candles, no honest 10-year M15 walk-forward claim is
possible from the supplied files.

## Tested model families

Two independently optimized London families were tested per symbol:

1. Trend-pullback continuation
   - H1 EMA20/EMA50 trend
   - H1 ADX threshold
   - M15 pullback toward EMA20
   - body, volume and RSI confirmation
   - break of the previous two-bar extreme

2. London opening-range breakout
   - Asian/early-London range variants
   - optional H1 trend filter
   - ATR breakout buffer
   - body, volume and RSI confirmation

Exit parameters were optimized independently:

- stop: 1.25, 1.50 or 1.75 ATR
- target: 1.75R, 2.25R or 2.75R
- break-even: 1R or 1.25R
- maximum hold: 24 or 32 M15 bars
- fixed research risk: 0.15% per trade

## Walk-forward design

Each test used an 18-month training window followed by a six-month untouched
test window. The process rolled forward every six months:

- 2024 H1
- 2024 H2
- 2025 H1
- 2025 H2
- 2026 H1

No test-window result was used to choose that same window's parameters.

## EURUSD result

EURUSD failed both model families.

### Trend-pullback walk-forward test windows

| Test window | Net | Trades | PF | Max DD |
|---|---:|---:|---:|---:|
| 2024 H1 | -$102.70 | 46 | 0.59 | 2.99% |
| 2024 H2 | -$103.78 | 45 | 0.59 | 2.18% |
| 2025 H1 | -$78.37 | 30 | 0.52 | 1.73% |
| 2025 H2 | -$142.04 | 78 | 0.63 | 3.55% |
| 2026 H1 | -$91.78 | 60 | 0.72 | 2.67% |

Aggregate: **-$518.67**, 259 trades. Every test window lost money.

### Opening-range walk-forward test windows

| Test window | Net | Trades | PF | Max DD |
|---|---:|---:|---:|---:|
| 2024 H1 | -$11.64 | 50 | 0.95 | 2.13% |
| 2024 H2 | -$84.91 | 30 | 0.51 | 1.92% |
| 2025 H1 | -$10.65 | 54 | 0.95 | 1.33% |
| 2025 H2 | -$18.54 | 54 | 0.93 | 2.56% |
| 2026 H1 | -$55.39 | 53 | 0.79 | 2.02% |

Aggregate: **-$181.13**, 241 trades. No EURUSD London model passed.

### EURUSD decision

EURUSD is **rejected** for this London framework. No live or approval-mode model
was created because optimization could not produce a positive untouched window.

## GBPJPY result

GBPJPY produced profitable training periods but unstable test periods.

### Trend-pullback walk-forward test windows

| Test window | Net | Trades | PF | Max DD |
|---|---:|---:|---:|---:|
| 2024 H1 | +$62.72 | 80 | 1.20 | 1.08% |
| 2024 H2 | -$31.84 | 34 | 0.81 | 1.49% |
| 2025 H1 | -$80.08 | 76 | 0.74 | 2.81% |
| 2025 H2 | +$136.68 | 54 | 1.83 | 1.79% |
| 2026 H1 | -$130.77 | 66 | 0.60 | 3.06% |

Aggregate: **-$43.31**, 310 trades. Two of five test windows were profitable.

### Opening-range walk-forward test windows

| Test window | Net | Trades | PF | Max DD |
|---|---:|---:|---:|---:|
| 2024 H1 | +$59.75 | 20 | 1.80 | 0.74% |
| 2024 H2 | -$91.61 | 25 | 0.28 | 2.36% |
| 2025 H1 | -$14.93 | 64 | 0.94 | 2.19% |
| 2025 H2 | -$70.99 | 78 | 0.79 | 2.40% |
| 2026 H1 | -$110.40 | 66 | 0.61 | 2.38% |

Aggregate: **-$228.18**, 253 trades. Only one of five test windows was
profitable.

### GBPJPY decision

GBPJPY is **not robust enough** for a continuously enabled London model. Its
training edge repeatedly disappeared in the following six-month test period.

The most efficient production decision is therefore to keep GBPJPY London
**disabled** and retain only research alerts until a genuinely stable regime
filter is discovered and validated on additional data.

## Final decision

Neither EURUSD nor GBPJPY passed the independent walk-forward requirement.
Creating a live model merely because an optimized training result looked good
would be curve-fitting.

Recommended portfolio state:

- GBPUSD V4 Swing: retain existing validated core
- GBPUSD Satellite V2: research/demo only under existing controls
- EURUSD London: disabled
- GBPJPY London: disabled
- GBPJPY defensive New York: research-only, because recent performance weakened

The next research iteration should test different explanatory features rather
than a larger parameter grid: session volatility regime, prior-day range,
weekly trend, event-day classification, and symbol-specific spread/ATR states.
