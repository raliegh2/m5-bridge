# M15/M30 multifamily research result

Status: **NO LIVE PROMOTION — PAPER FORWARD TEST ONLY**

Six families were compared with a 50% development / 25% family-selection /
25% untouched-final split. Every test used completed bars, next-M5-open entry,
0.25% risk, one position at a time, and conservative costs.

## Closest candidate

`TREND_REENTRY_ONLY` was the closest stable candidate before final testing:

| Segment | Net | Average/week | Profit factor | Trades |
|---|---:|---:|---:|---:|
| Development | +$96.52 | +$5.36 | 1.276 | 59 |
| Family selection | +$95.28 | +$10.84 | 1.427 | 36 |
| Untouched final | **-$12.84** | **-$1.43** | **0.942** | 33 |

The $50/week target was not achieved. At the current $5,000 research balance,
even increasing risk from 0.25% to the hard 1% ceiling would scale both profits
and losses roughly fourfold; it would not turn negative final expectancy into
a valid edge.

## Other findings

- Breakout-only failed in family selection.
- Range reversion failed in family selection.
- The regime ensemble increased trade count but failed in family selection.
- London ORB changed sharply between segments and failed the final diagnostic.
- The original mixed trend model was positive in the first 75% and negative in
  the untouched final 25%.

`v12_intraday_paper_bot.py` therefore logs the closest trend-reentry signals for
forward observation but contains no `order_send()` path. It must accumulate a
larger forward sample and pass profit-factor/drawdown gates before execution is
considered.
