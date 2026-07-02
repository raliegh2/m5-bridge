# V10 GBPUSD Swing Precision Backtest

## Result

The V10 portfolio implementation was retained. The GBPUSD swing component was
upgraded with completed-H4 signal quality gates and setup-specific risk tiers.

| Metric | V10 swing baseline | Precision candidate | Change |
|---|---:|---:|---:|
| Ending balance | $5,803.57 | **$6,105.49** | +$301.92 profit |
| Net profit | $803.57 | **$1,105.49** | **+37.57%** |
| Return | 16.07% | **22.11%** | +6.04 pp |
| Trades | 125 | 93 | -32 |
| Win rate | 67.20% | **76.34%** | +9.14 pp |
| Profit factor | 2.1036 | **4.2326** | +2.1291 |
| Maximum drawdown | 1.7472% | **1.4927%** | -0.2545 pp |

## Precision rules

- **Primary 16 UTC breakout, A grade:** volume ratio at least 1.248 and candle
  range at least 1.555 ATR; risk 0.50%.
- **Primary 16 UTC breakout, B grade:** valid primary signal without the full
  expansion profile; risk reduced to 0.20%.
- **Secondary 12 UTC breakout:** requires ATR expansion of at least 1.018 and
  a directional candle body no larger than 1.473 ATR; risk 0.40%. Otherwise
  the entry is rejected as overextended or weak.
- **Pullback add-on:** directional EMA20-to-EMA50 separation must not exceed
  1.237 ATR; risk 0.40%. Wider separation is rejected as a late trend chase.

All measurements use the completed signal candle. No future candle is used.

## Development and validation

| Segment | Baseline profit | Precision profit | Baseline PF | Precision PF | Precision DD |
|---|---:|---:|---:|---:|---:|
| Before 2023 | $625.11 | **$792.14** | 2.459 | **5.716** | 0.812% |
| 2023 onward | $158.63 | **$270.50** | 1.595 | **2.801** | 1.493% |

The improvement persisted in the later validation segment rather than being
limited to the older development period.

## Cost stress

| Added cost per selected trade | Net profit | Return | PF | Max DD |
|---|---:|---:|---:|---:|
| 0.03R | $1,052.25 | 21.05% | 4.005 | 1.554% |
| 0.05R | $1,017.01 | 20.34% | 3.860 | 1.595% |
| 0.10R | $929.80 | 18.60% | 3.519 | 1.696% |

## Live implementation scope

`gbpusd_portfolio_v10.py` injects the precision gate into the currently live
V4 primary and secondary breakout families while retaining the V9 Satellite V3
filter, V2 portfolio caps, news checks, duplicate prevention, broker stops and
drawdown controls. The pullback add-on remains research-only because its
1.25-ATR stop, 2.5R target and 36-H4-bar management profile is not yet supported
by the live V4 position manager.

## Important limitation

This is a signal-selection and risk-tier replay over the exact raw-H4 trade
ledger. It improves the historical selection of swing signals, but it does not
prove that future entries will occur at the market's maximum-profit point.
The new policy should remain in READ_ONLY or demo mode until forward trades are
reconciled against expected fills.
