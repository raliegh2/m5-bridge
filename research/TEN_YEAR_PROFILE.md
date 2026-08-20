# Ten-year profitability and risk ceiling

Date: 2026-08-16

Window 2016-08-17 → 2026-08-17, H4, audited bars, **tight** cost tier (the most
favourable realistic pricing), $10,000 start, 0.5% risk per trade, locked
parameters. The window is simply the last ten years of the same series — nothing
was optimised for it.

## Profitability

| model | symbol | trades | net $ | CAGR | max DD | DD $ | PF | losing years |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| V16 | EURUSD | 907 | −563.62 | −0.58% | 29.20% | 3,125.82 | 0.968 | 4/11 |
| V16 | GBPUSD | 916 | −538.47 | −0.55% | 14.46% | 1,547.74 | 0.971 | 8/11 |
| **V16** | **AUDUSD** | 904 | **+905.04** | **+0.87%** | 15.54% | 1,565.89 | 1.050 | 6/11 |
| V16 | USDJPY | 913 | −860.60 | −0.90% | 19.71% | 1,991.28 | 0.954 | 6/11 |
| V16 | GBPJPY | 905 | −2,544.02 | −2.89% | 36.27% | 3,728.65 | 0.848 | 7/11 |
| V16 | XAUUSD | 937 | −2,065.74 | −2.29% | 25.32% | 2,532.42 | 0.901 | 6/11 |
| **V19** | **EURUSD** | 613 | **+116.70** | **+0.12%** | 15.77% | 1,634.17 | 1.010 | 4/11 |
| V19 | GBPUSD | 581 | −81.42 | −0.08% | 12.51% | 1,367.82 | 0.993 | 5/11 |
| V19 | AUDUSD | 568 | −1,013.82 | −1.06% | 14.64% | 1,464.41 | 0.904 | 8/11 |
| V19 | USDJPY | 558 | −944.87 | −0.99% | 15.61% | 1,569.22 | 0.916 | 8/11 |
| V19 | GBPJPY | 567 | −1,688.56 | −1.83% | 26.29% | 2,700.58 | 0.846 | 6/11 |
| V19 | XAUUSD | 596 | −1,676.05 | −1.82% | 18.72% | 1,875.03 | 0.874 | 8/11 |

**2 of 12 configurations were profitable.** Best case: **+$905 on $10,000 over
ten years — +0.87% a year**, and that on the single most favourable
symbol/model pair found after 1,385 recorded trials.

Year by year for that best case (V16 AUDUSD):

| year | P&L | trades |
|---|---:|---:|
| 2016 | −288.50 | 28 |
| 2017 | −184.85 | 98 |
| 2018 | −81.93 | 95 |
| 2019 | −172.61 | 89 |
| 2020 | −720.96 | 95 |
| 2021 | +92.64 | 87 |
| 2022 | **+993.68** | 83 |
| 2023 | +524.88 | 89 |
| 2024 | +804.48 | 92 |
| 2025 | +69.35 | 91 |
| 2026 | −131.05 | 57 |

**Six losing years out of eleven, and five consecutive losing years to start.**
The entire ten-year profit was earned in 2022–2024. An account opened in 2016
would have been down roughly 14% by the end of 2020 with no way to know whether
the recovery was coming.

## The comparison that matters

| | return | drawdown risk |
|---|---:|---:|
| best configuration found | **+0.87%/yr** | **15.54%** |
| a deposit account | ~4–5%/yr | 0% |

Return per unit of drawdown is **0.056**. The strategy is dominated outright by
cash: several times less return for meaningful risk of loss. That is the
practical answer to "is it worth trading" independent of any statistical gate.

## Risk ceiling

### What the system enforces (`mt5_ai_bridge/risk_v18.py`)

| control | limit |
|---|---:|
| per trade | 2.0% of equity |
| aggregate open risk | 6.0% |
| per currency | 4.0% |
| concurrent positions | 5 |
| daily loss kill switch | 2.0% |
| **total drawdown kill** | **20.0%** (latches, does not clear on recovery) |
| consecutive losses | 5 |
| exposure taper begins | 5% drawdown |
| exposure taper floor | 25% of normal size at 20% |

**The hard ceiling is 20% of equity — $2,000 on $10,000.** At that point the
kill switch latches and stays latched. Reaching it needs roughly ten consecutive
maximum-size full-stop losses; the five-loss cutout and the exposure taper are
both there to make that unreachable in practice.

### What actually happened, and the important caveat

The backtests above run the raw candidates at a fixed 0.5% risk **without** the
V18 risk engine — no governor, no kill switches. So the realised drawdowns show
what the strategies do *unprotected*:

* deepest drawdown **36.27%** (V16 GBPJPY, $3,728)
* longest underwater run **881 consecutive trades**
* **4 of 12 configurations breached the 20% kill switch** — V16 EURUSD, V16
  GBPJPY, V16 XAUUSD, V19 GBPJPY

With the risk engine attached, those four would have been shut down at 20% and
never recovered, because the drawdown trip latches. That is the system working
as designed: it converts a 36% loss into a 20% stop. It does not convert a
losing strategy into a winning one.

Note also the 881-trade underwater run. At roughly 90 trades a year that is
close to a decade below a prior peak — long enough that any human would abandon
the system regardless of what the backtest eventually shows.

## Summary

* **Profitability over ten years:** +0.87% a year at best, 2 of 12
  configurations positive, six losing years in eleven, all profit concentrated
  in 2022–2024.
* **Risk ceiling:** 20% of equity by design and latched; 36% realised without
  that protection, with a third of configurations breaching the ceiling.
* **Verdict:** the risk ceiling is sound and does its job. The return does not
  justify using it. Cash pays several times more with none of the drawdown.

835 tests pass.
