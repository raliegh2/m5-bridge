# V14.6.1 Multi-Entry Intraday ICT Trend Research

**Window:** 2012-03-05 to 2022-03-05
**Starting balance:** $5,000.00
**Retail-net target:** $34,000.00

## ICT validation

| Symbol | V14.6 ICT | V14.6.1 ICT | Selected engine | Active-day average | Max entries/day |
|---|---|---|---|---:|---:|
| GBPUSD | Failed | False | - | 0.00 | 0 |
| GBPJPY | Failed | True | GBPJPY_ICT_INTRADAY_GJ_LONDON_PULLBACK | 1.00 | 1 |
| AUDUSD | Failed | True | AUDUSD_ICT_INTRADAY_AU_ASIA_LONDON_PULLBACK | 1.00 | 1 |

## Safe portfolio comparison

| Metric | V14.6 | V14.6.1 |
|---|---:|---:|
| Net profit | $6,282.97 | $8,069.70 |
| Ending balance | $11,282.97 | $13,069.70 |
| Profit factor | 1.9769 | 1.9429 |
| Closed drawdown | 8.5052% | 8.7969% |
| Stress drawdown | 9.4176% | 9.9792% |
| Target reached | False | False |

## Controls

- Entries use completed H1 signals and the next H1 open; forming candles are never used.
- Target symbols may hold at most two ICT positions and admit at most one new trade per hour.
- Each profile caps candidate generation at five to seven entries per day.
- Partial profit and break-even logic is simulated conservatively with stop-first ordering inside ambiguous candles.
- The 1.75% ICT cap, 3.25% combined cap and 7.5/8.5/9.0/9.6 drawdown governor remain active.

Research only. Historical R-cost results do not guarantee broker-native profitability.
