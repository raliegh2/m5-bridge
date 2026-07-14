# Strategy Engine V8 Backtest Verification

## GBPUSD swing correction

The V8 portfolio retains the tested `GBPUSD_SWING_V6` allocation instead of the earlier low-income swing profile.

| Metric | Previous profile | V8 swing allocation |
|---|---:|---:|
| One-year net profit | $57.04 | $104.40 |
| Improvement | - | $47.36 / 83.03% |
| Trades | 7 | 7 |
| Win rate | 85.71% | 85.71% |
| Profit factor | 4.73 | 4.51 |
| Risk per swing trade | 0.35% | 0.50% |

The higher allocation remains bounded by the portfolio's 0.75% total open-risk cap.

## Verified synchronized result

| Portfolio metric | Result |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $5,805.46 |
| Net profit | $805.46 |
| Return | 16.11% |
| Accepted trades | 217 |
| Profit factor | 1.5946 |
| Win rate | 43.32% |
| Realized maximum drawdown | 3.0020% |
| Full-open-risk stress drawdown | 3.4870% |
| Rejected candidates | 7 |
| Rejection reason | `max_open_risk` |

## Engine contribution

| Engine | Trades | Net profit | Profit factor | Win rate |
|---|---:|---:|---:|---:|
| EURUSD Satellite V7 | 22 | $136.11 | 2.0616 | 50.00% |
| GBPJPY Satellite V7 | 18 | $151.47 | 3.5607 | 55.56% |
| GBPUSD Satellite V2 | 170 | $413.48 | 1.3635 | 39.41% |
| GBPUSD Swing V6 | 7 | $104.40 | 4.5106 | 85.71% |

## Verification

```text
PYTHONPATH=. pytest -q
6 passed
```

The independent replay reproduced the supplied ending balance, profit factor, win rate, realized drawdown, and open-risk stress drawdown.

## Limitations

- This is a synchronized trade-ledger replay, not a fresh tick-level execution test.
- The raw GBPUSD H4 OHLC file was not available as an accessible upload during this run; the earlier validation report and generated trade ledgers were available.
- Historical macro-event coverage remains incomplete beyond the supplied FOMC subset.
- Historical performance does not guarantee live results. Keep the branch in demo/forward testing until the frozen profile passes its review criteria.
