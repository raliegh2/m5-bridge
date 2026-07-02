# V12 Horizontal-Scale Backtest

The new horizontal controls were replayed over 10-, 5-, 3- and 2-year windows using the existing GBPUSD, EURUSD and GBPJPY candidate ledgers.

## Results on a $5,000 starting balance

| Window | Net profit | Return | Average monthly profit | Trades | Profit factor | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 10 years | $1,454.81 | 29.10% | $12.13 | 676 | 1.388 | 3.49% | 4.17% |
| 5 years | $1,247.35 | 24.95% | $20.79 | 308 | 1.771 | 2.24% | 2.63% |
| 3 years | $771.44 | 15.43% | $21.42 | 168 | 1.939 | 1.63% | 1.78% |
| 2 years | $689.68 | 13.79% | $28.76 | 115 | 2.319 | 1.29% | 1.69% |

## Comparison with previous V12 limits

| Window | Previous profit | Horizontal profit | Difference |
|---|---:|---:|---:|
| 10 years | $2,383.83 | $1,454.81 | -$929.02 |
| 5 years | $1,769.66 | $1,247.35 | -$522.31 |
| 3 years | $1,023.99 | $771.44 | -$252.55 |
| 2 years | $904.51 | $689.68 | -$214.83 |

The new safety layer reduced drawdown slightly but reduced profit and accepted trades materially. Over ten years it rejected 78 candidates through the four-hour stagger, 44 through the basket cap, 40 through the lower per-symbol cap, 126 through the adaptive guard and one through the GBP correlation cap.

Only GBPUSD, EURUSD and GBPJPY had validated historical candidate ledgers. The replay therefore could not measure the intended income contribution from additional commodity-block and safe-haven assets. The branch should remain in READ_ONLY while those strategies are researched.
