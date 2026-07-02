# Strategy Engine V8 synchronized portfolio backtest

## Objective

- synchronize GBPUSD Satellite V2, EURUSD Satellite V7, GBPJPY Satellite V7
  and the GBPUSD swing engine on one $5,000 portfolio
- enforce entry conflicts, aggregate open risk and GBP correlation limits
- increase GBPUSD swing income without exceeding the 0.50% per-trade ceiling

## GBPUSD Swing V6 runner

The frozen V4 entry rules remain unchanged. The research runner profile uses:

- 0.50% risk on the V4 core and H4 pullback add-on
- 33% partial at 1R instead of 50%
- 4.5R final target
- trailing begins at 2R
- 3.5 ATR trailing distance
- maximum 108 H4 bars, approximately 18 trading days

A longer hold was tested against the current management. It improved the latest
one-year core result but did not improve every long-term metric. The profile is
therefore a research candidate, not a replacement for the frozen V4 configuration.

### Exact frozen-entry runner test

| Window | Net | Trades | PF | Max DD |
|---|---:|---:|---:|---:|
| Full 2016-Jul 2026 | +$857.20 | 93 | 2.21 | 2.65% |
| Latest year | +$71.81 | 6 | 3.82 | 1.31% |

The synchronized portfolio also included the existing H4 pullback add-on. Under
portfolio compounding, the complete swing engine produced +$104.40 from seven
accepted one-year trades.

## Synchronized portfolio controls

- starting balance: $5,000
- maximum three positions
- maximum aggregate open risk: 0.75%
- GBP aligned exposure cap: 0.75%
- mixed GBP-direction exposure cap: 0.50%
- aligned GBPUSD swing and satellite trades may coexist
- daily loss limit: $250
- weekly loss limit: 4%
- total loss limit: $500
- risk throttle at 6% drawdown
- full pause at 10% drawdown
- swing signals receive entry priority

## One-year synchronized result

| Metric | Result |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $5,805.46 |
| Net profit | +$805.46 |
| Return | +16.11% |
| Accepted trades | 217 |
| Profit factor | 1.59 |
| Win rate | 43.32% |
| Realized-equity max DD | 3.00% |
| Open-risk stress max DD | 3.49% |
| Maximum concurrent positions | 2 |
| Maximum open risk | 0.75% |
| Rejected candidates | 7 |

All seven rejected candidates were blocked by the 0.75% aggregate open-risk cap.
No daily, weekly, total-loss or drawdown-pause circuit breaker was triggered.

## Income by engine after synchronization

| Engine | Accepted trades | Net profit | PF |
|---|---:|---:|---:|
| GBPUSD Satellite V2 | 170 | +$413.48 | 1.36 |
| EURUSD Satellite V7 | 22 | +$136.11 | 2.06 |
| GBPJPY Satellite V7 | 18 | +$151.47 | 3.56 |
| GBPUSD Swing V6 | 7 | +$104.40 | 4.51 |
| **Complete portfolio** | **217** | **+$805.46** | **1.59** |

## Swing-income improvement

The previous one-year GBPUSD Swing V5 result was approximately +$57.04.
The synchronized V6 runner produced +$104.40.

- dollar improvement: +$47.36
- percentage improvement: approximately +83.0%

The increase came from the maximum permitted 0.50% risk, a smaller partial exit,
a later and wider trailing stop, a 4.5R target and a longer maximum hold.

## Important limitations

This is a synchronized **trade-candidate and risk-reservation backtest**. It uses
the historical R outcome of each strategy trade and recalculates dollar P/L from
the shared portfolio balance and risk reserved at entry.

The open-risk stress curve marks every open trade at its full reserved loss. It
is conservative, but it is not an exact tick-by-tick mark-to-market equity curve.
A final MT5 test still needs the original entry, stop, partial and price-path data
for every strategy. Complete historical economic-event coverage was also not
available.

## Decision

- keep all V8 engines disabled by default
- keep GBPUSD Swing V6 in READ_ONLY mode
- keep EURUSD V7 in READ_ONLY mode
- keep GBPJPY V7 in approval/forward-demo mode
- do not merge for unattended execution until local tests, exact MTM portfolio
  replay, event coverage and forward-demo reconciliation are completed
