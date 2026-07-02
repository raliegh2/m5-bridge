# V13 Horizontal Expanded-Asset Backtest

## Architecture tested

V13 preserves the V10 GBPUSD precision tiers while adding two independently validated horizontal engines:

- **AUDUSD commodity block:** completed D1/H4 EMA pullback continuation, accepted only at 04:00 or 08:00 UTC; 0.25% risk, 1.25 ATR stop, 2R target, 1.5 ATR trail and a 20 H4-bar maximum hold.
- **USDJPY safe haven:** completed D1 trend plus H4 40-bar breakout; 0.25% risk, 1.5 ATR stop, 3R target, 2 ATR trail and a 30 H4-bar maximum hold.

Portfolio controls remain five positions, 1.50% total open risk, 0.40% generic per-symbol risk, 0.65% dedicated GBPUSD precision capacity, 0.90% aligned GBP exposure and 0.65% mixed GBP exposure. The four-hour stagger is applied per basket rather than globally.

## Independent validation

| Engine | Segment | Trades | Net R | Profit factor |
|---|---|---:|---:|---:|
| AUDUSD trend pullback | Development | 176 | 37.56R | 1.508 |
| AUDUSD trend pullback | Final validation | 79 | 20.63R | 1.665 |
| USDJPY breakout | Development | 354 | 39.35R | 1.234 |
| USDJPY breakout | Final validation | 186 | 22.79R | 1.252 |

The original generic AUDUSD and USDCAD breakout candidates failed their final validation gates and were not admitted. The approved AUDUSD implementation is a different trend-pullback family with a development-selected 04/08 UTC quality filter.

## Synchronized replay on a $5,000 starting balance

The public common-history period is 26 November 2012 through 4 March 2022, approximately 9.27 years. The maximum-history row is therefore not a full ten calendar years and is not current through 2026.

| Window | Net profit | Return | Average monthly | Trades | Profit factor | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Maximum common history | **$1,884.17** | **37.68%** | **$16.94** | 1,071 | 1.266 | 5.76% | 6.00% |
| 5 years | **$522.27** | **10.45%** | **$8.71** | 588 | 1.153 | 5.46% | 5.89% |
| 3 years | **$394.29** | **7.89%** | **$10.95** | 360 | 1.195 | 4.10% | 5.01% |
| 2 years | **$160.63** | **3.21%** | **$6.70** | 250 | 1.112 | 4.10% | 5.01% |

## Improvement over the same replay without AUDUSD

| Window | Previous four-symbol profit | V13 five-symbol profit | Increase |
|---|---:|---:|---:|
| Maximum common history | $1,385.90 | **$1,884.17** | **+$498.26** |
| 5 years | $248.60 | **$522.27** | **+$273.67** |
| 3 years | $129.91 | **$394.29** | **+$264.39** |
| 2 years | $62.34 | **$160.63** | **+$98.29** |

The maximum-history realized drawdown increased from 5.02% to 5.76%, while stress drawdown increased from 5.88% to 6.00%.

## Maximum-history contribution

| Engine | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| GBPUSD V10 precision | 88 | **$1,048.38** | 2.727 |
| AUDUSD trend pullback | 215 | **$465.90** | 1.321 |
| USDJPY safe-haven breakout | 213 | **$283.91** | 1.175 |
| GBPJPY H4 engine | 347 | **$83.36** | 1.041 |
| EURUSD H4 engine | 208 | **$2.63** | 1.002 |

The expanded engine improved profit in all requested trailing windows relative to the same four-symbol replay, but it did not reach $100 per month on a $5,000 account. Keep the candidate in READ_ONLY and then demo reconciliation. This is an OHLC/candidate-ledger replay rather than tick-level broker execution.
