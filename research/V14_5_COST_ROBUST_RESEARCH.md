# V14.5 Cost-Robust Reallocation — Research Report and 10-Year Backtest

July 2026. Follow-up to the V14.4 live profit guard and the July demo-loss
diagnosis. Goal: find where this system has a durable, cost-surviving edge
and re-run the 10-year benchmark honestly (with costs) instead of at zero
cost.

## 1. Method

Data (all already in this repository):

| Stream | Trades | Span | Stops |
| --- | --- | --- | --- |
| GBP ICT M1 candidates (`v14_3_under10_target_out/deduped_liquidity_fade_gap60.csv`) | 11,649 | 2023-01 → 2026-07 | 5.0–7.5 pip floors |
| V12 swing ledger (inside `true_combined_closed_trades.csv`) | 918 | 2013-02 → 2022-03 | ATR-based, ~40–90 pips |

Validation protocol (to avoid repeating the overfitting that produced the
Tuesday-exclusion style filters): ICT rules were designed on 2023–2024,
confirmed on 2025, tested once on 2026; V12 engines were split 2013–2018
vs 2019–2022. Costs modeled as spread/stop in R: ICT 0.075R (demo) /
0.13R (standard retail), swing 0.02–0.03R.

## 2. Finding 1 — the ICT M1 scalp engine is structurally cost-broken

Raw candidate expectancy by year (zero cost): 2023 +0.050R/trade,
2024 +0.023R, **2025 −0.007R (negative before any costs)**, 2026 +0.069R.

Six pre-registered filter variants (current locked live rules,
sweeps-only, per-symbol setup whitelists, London/NY session windows,
core-hours) were evaluated. The current locked rules were the best
variant — and still fail:

| Variant (best = live locked rules) | 2023-24 net R | 2025 net R |
| --- | --- | --- |
| zero cost | +198 | +56 |
| demo cost (0.075R) | +44 | **−17** |
| retail cost (0.13R) | −87 | **−79** |

Expectancy of +0.05..0.08R per trade cannot pay 0.08–0.16R per trade in
costs. No filter fixes that; only wider stops (a different engine,
requiring M1 price data not present in the repo) or better raw signals
could.

## 3. Finding 2 — the durable edge is in the V12 swing engines

Per-trade expectancy (zero cost), in-sample 2013–2018 vs out-of-sample
2019–2022:

| Engine | IS exp | OOS exp | Verdict |
| --- | --- | --- | --- |
| GBPUSD_V10_PRECISION | +0.50R | +0.56R | **promote** |
| GBPJPY_SWING_CORE | +0.16R | +0.42R | **promote** |
| AUDUSD_TREND_PULLBACK | +0.14R | +0.19R | **promote** |
| EURUSD_SWING_CORE | +0.08R | +0.32R | **promote** |
| GBPUSD_SWING_RETEST | +0.06R | +0.04R | demote (≈ cost level) |
| EURUSD_SWING_RETEST | +0.37R | −0.19R | demote (flipped) |
| USDJPY_SAFE_HAVEN_BREAKOUT | +0.17R | −0.15R | demote (flipped) |

Promotion rule (pre-registered): positive expectancy in both halves and
≥0.07R (≈3× swing cost) in both. With 28-hour median holds and 40–90 pip
stops, these engines pay ~0.02–0.03R in costs against 0.14–0.56R edges —
a 5–20× margin, versus the ICT engine's 0.5× margin.

## 4. The V14.5 model (three parameters)

1. Promoted swing engines trade at **0.75%** risk (under the 0.80% parity ceiling).
2. Demoted V12 engines: dropped from the benchmark (live: micro observation).
3. GBP ICT M1: **0.025% observation risk** only, feeding the V14.4 expectancy tracker.

Unchanged: 3.25% combined open-risk cap, parity drawdown governor, all
V14.4 live guards, demo-only transmission.

## 5. Exact ten-year backtest (2016-07-03 → 2026-07-03, $5,000 start)

`python research/v14_5_cost_robust_backtest.py`
(replay engine reproduces the official zero-cost baseline within 0.04%)

| Scenario | Net profit | PF | Max DD | CAGR |
| --- | --- | --- | --- | --- |
| Baseline, zero cost (the old headline) | +$17,033 | 1.157 | 8.97% | 16.0% |
| **Baseline, demo cost** | +$2,580 | 1.126 | 11.7% | 4.3% |
| **Baseline, retail cost** | +$992 | 1.054 | 15.0% | 1.8% |
| V14.5, zero cost | +$6,059 | 1.469 | 9.5% | 8.3% |
| **V14.5, demo cost** | +$3,170 | 1.272 | 9.9% | 5.1% |
| **V14.5, retail cost** | +$1,865 | 1.202 | 14.9% | 3.2% |

Reading:

- The old +$17k headline exists only at zero cost. With costs the current
  model collapses to PF 1.05–1.13. This is why the demo account lost money.
- V14.5 beats the baseline in **every cost-bearing scenario** (+23% net at
  demo costs, +88% at retail costs) with far higher profit factor.
- V14.5's 2023–2026 segment is nearly flat because the V12 ledger ends in
  March 2022 — the swing engines have no research data after that, while
  live they would keep trading. Over the swing-covered era (2016-07 →
  2022-03) V14.5 at demo cost compounds ~9%/yr with <10% drawdown.
- Even at 0.025% observation size, the ICT stream loses ~$408 over
  3.5 years at retail costs — supporting observation-only status.

## 6. Recommendations

1. Adopt the V14.5 allocation (see `mt5_ai_bridge/v14_5_cost_robust_profile.py`).
2. Keep every V14.4 guard active; the expectancy tracker now doubles as
   the ICT engine's probation mechanism.
3. To ever revive the ICT engine: re-generate candidates from M1/M5 price
   data with ≥15-pip stops (cost ≤0.07R) and re-run this protocol. Filters
   alone cannot save it.
4. Export fresh V12-engine history (2022–2026) so the swing sleeve's
   recent performance can be validated the same way.

## 7. Limitations

R-multiple replay, not tick simulation; costs are constant per stream
rather than per-trade; the combined stream is V12 (2013–2022) + ICT
(2023–2026) with no overlap year; swing-engine promotion used one IS/OOS
split, not rolling walk-forward (918 trades limit the granularity);
slippage beyond spread, swap on 28h holds, and weekend gaps are
approximated inside the cost constants. Expect live results below
backtest (published-strategy decay averages 26–58%).

## Sources

- Osler (2002), NY Fed — stop-loss order clustering: the mechanism behind sweep/reclaim setups
- Krohn et al. (2024), Journal of Finance — FX fixings and around-the-clock returns (intraday edges are basis-point scale)
- Bailey & López de Prado — The Deflated Sharpe Ratio (multiple-testing correction)
- McLean & Pontiff — out-of-sample decay of published predictors (−26% OOS, −58% post-publication)
- Retail spread surveys 2025–26 (BrokerChooser, IC Markets, Investing.com): GBPUSD 0.6–1.2 pips standard, GBPJPY 1.5–2.5 pips
