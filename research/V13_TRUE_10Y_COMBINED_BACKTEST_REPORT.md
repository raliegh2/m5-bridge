# V13 true synchronized V12 Final + V11 intraday backtest

Status: **FAILED — DO NOT MERGE V11 INTRADAY INTO V12 FINAL**

Branch: `v13-v12-final-plus-v11-intraday`  
PR: #36  
Synchronized period: 2012-11-26 through 2022-03-04 (about 9.27 years)

## Method

- Regenerated V11 signals from public 10-year M15/M30/H1 history for GBPUSD,
  EURUSD and GBPJPY.
- Used completed candles and next-M15-bar entries.
- Used conservative stop-first ordering when stop and target were both touched.
- Forced V11 positions flat by 20:00 UTC and applied daily entry caps.
- Deducted 0.05R per V11 trade for spread/slippage stress.
- Regenerated the exact V12 Final candidates and confirmed the $3,201.58 parity.
- Merged all candidates chronologically and replayed them through the shared
  V12/V13 position, open-risk, symbol and GBP-correlation limits.

## Headline result

| Scenario | Net profit | Return | Trades | PF | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|
| V12 Final only | **+$3,201.58** | 64.03% | 918 | 1.606 | 4.93% | 5.25% |
| V11 intraday only | **-$543.79** | -10.88% | 241 | 0.627 | 11.14% | 11.27% |
| V13 combined | **+$2,388.93** | 47.78% | 1,198 | 1.328 | 5.98% | 6.45% |

Combined performance is **$812.65 worse** than V12 Final alone.

## V11 attribution inside the combined replay

| Engine | Trades | Net profit | PF |
|---|---:|---:|---:|
| GBPUSD V11 intraday | 110 | -$129.17 | 0.827 |
| EURUSD V11 intraday | 59 | -$270.83 | 0.444 |
| GBPJPY V11 intraday | 111 | -$197.67 | 0.822 |

V11 displaced zero accepted V12 trades. The combined result is nevertheless
worse because V11 loses money and reduces the balance used to size later V12
trades.

## Decision

The earlier +$4,248.47 figure was an additive estimate using mismatched windows,
not a synchronized raw-price backtest. The true replay rejects that estimate.
Keep V12 Final unchanged and do not promote the current V11 intraday engines.

## Limitations

- The common synchronized period is 9.27 years rather than a full ten because
  it is bounded by the V12 five-symbol common history.
- M15 OHLC cannot reveal tick order inside a candle, so ambiguous bars use the
  conservative stop-first assumption.
- Broker-specific spread and slippage are represented by a fixed 0.05R cost.
- Public-source timestamps and prices can differ from the intended broker feed.
