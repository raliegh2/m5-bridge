# V15 multi-symbol design: what the structure permits

Date: 2026-08-16

The goal was a model that stays consistent and profitable across several
symbols. This documents the design, and the three measurements that determine
whether it can work at all. Two of them say it currently cannot, and both are
facts about the market rather than about the code — which is why no amount of
parameter work would have found them.

## The design

Three structural mechanisms, each standard practice rather than a fitted choice.
None of them is tuned, and none can be tuned, which is the point.

### 1. Volatility targeting

Each position is sized so its risk contribution is the same fraction of equity:

    lots = (balance * risk_pct) / (stop_pips * pip_value_per_lot)

with `stop_pips` derived from that instrument's own ATR. Without it, gold's ATR
dwarfs AUDUSD's and a nominally equal-weighted basket is really one gold bet.

### 2. Correlated-exposure caps

`PortfolioConfig` limits aggregate open risk (2.0%), per-currency risk (1.5%)
and concurrent positions (4). The per-currency cap is the one that binds here:
four simultaneous long-EUR/GBP/AUD/XAU positions are all short USD, which is one
leveraged bet wearing four hats.

### 3. Walk-forward symbol admission

Fold *n* admits only symbols that were profitable across folds `0..n-1`.
Fold 0 admits everything, because nothing is known yet. Optionally
(`--require-persistence`) a symbol must also show significant trend persistence.

This is the honest form of "trade what works". Choosing the winning symbols
after seeing the whole history is precisely the selection bias that produced
v4…v14.25.

## Measurement 1 — none of these markets trends

A momentum model is a bet that returns are positively autocorrelated over the
holding period. `mt5_ai_bridge/persistence.py` tests that directly with the
Lo–MacKinlay variance ratio, before any strategy runs.

H4 log returns, 1999–2026 (`research/v15_persistence_scan.py`):

| symbol | VR q=2 | q=6 | q=30 | q=120 | Hurst | verdict |
|---|---:|---:|---:|---:|---:|---|
| AUDUSD | 0.992 | 0.987 | 0.935 | 0.909 | 0.487 | random walk |
| EURUSD | 0.993 | 0.989 | 0.962 | 0.962 | 0.495 | random walk |
| GBPJPY | 0.978 | 0.963 | 0.958 | 0.896 | 0.494 | random walk |
| GBPUSD | 0.990 | 0.979 | 0.990 | 0.910 | 0.497 | random walk |
| USDJPY | 0.998 | 0.994 | 0.943 | 0.938 | 0.488 | random walk |
| XAUUSD | 1.009 | 1.044 | 1.031 | 0.963 | 0.500 | random walk |

**Not one symbol is significantly above 1.0 at any horizon.** Most sit slightly
*below* it, i.e. faintly mean-reverting. The effect V15 is built to harvest is
not measurably present in these instruments.

This also explains the XAUUSD backtest result (PF 1.207, every fold positive):
it has no structural support, which is exactly what a deflated Sharpe of 0.45
was already saying. Two independent methods now agree it was luck.

⚠️ **A warning about the earlier run.** Including MetaQuotes' pre-1999 bars,
EURUSD and USDJPY appeared *significantly trending* (VR 1.256 and 1.310 at
q=120, p < 0.05). That signal was entirely an artifact of fabricated history —
the euro did not exist before 1999. Dropping those bars removes it completely.
Any result computed on this data must exclude pre-1999 bars.

## Measurement 2 — this symbol set barely diversifies

Diversification is the one genuine free lunch available, but it is a
**multiplier on an existing edge, not a source of one**. N imperfectly
correlated streams behave like

    effective_bets = 1 / (w' R w)

independent bets, and scale Sharpe by roughly its square root.

H4 log-return correlations, 1999–2026:

|  | AUDUSD | EURUSD | GBPJPY | GBPUSD | USDJPY | XAUUSD |
|---|---:|---:|---:|---:|---:|---:|
| **AUDUSD** | 1.000 | 0.584 | 0.367 | 0.557 | -0.077 | 0.340 |
| **EURUSD** | 0.584 | 1.000 | 0.271 | 0.644 | -0.284 | 0.355 |
| **GBPJPY** | 0.367 | 0.271 | 1.000 | 0.637 | 0.654 | 0.015 |
| **GBPUSD** | 0.557 | 0.644 | 0.637 | 1.000 | -0.163 | 0.274 |
| **USDJPY** | -0.077 | -0.284 | 0.654 | -0.163 | 1.000 | -0.249 |
| **XAUUSD** | 0.340 | 0.355 | 0.015 | 0.274 | -0.249 | 1.000 |

| set | symbols | effective bets | Sharpe multiplier |
|---|---:|---:|---:|
| all exported | 6 | **2.60** | ×1.61 (vs ×2.45) |
| priceable only | 4 | **1.68** | ×1.30 (vs ×2.00) |

The structure is three blocs, not six symbols: a **USD bloc**
(EURUSD/GBPUSD/AUDUSD, ρ ≈ 0.56–0.64), a **JPY bloc** (GBPJPY/USDJPY,
ρ = 0.654), and **gold**, which is the only genuine diversifier (ρ = 0.015 to
0.355, and −0.249 against USDJPY).

Because the two JPY pairs are currently unpriceable, the tradeable set is four
symbols that all quote USD — **1.68 effective bets**. Adding more USD majors
would add almost nothing.

## Measurement 3 — the portfolio, run honestly

`research/v15_portfolio_test.py`, H4, 1999–2026, typical costs:

```
fold 0: net=-1380.34  trades=612  dd=23.28%  admitted=4 [AUDUSD,EURUSD,GBPUSD,XAUUSD]
fold 1: NO SYMBOLS ADMITTED (none profitable across folds 0..0)
fold 2: NO SYMBOLS ADMITTED
fold 3: NO SYMBOLS ADMITTED
fold 4: NO SYMBOLS ADMITTED
```

| metric | value |
|---|---:|
| net profit | -1,380.34 |
| profit factor | 0.892 |
| folds positive | 0 of 5 |
| max drawdown (fold 0) | 23.28% |
| deflated Sharpe | 0.157 |
| verdict | **FAIL** (4 of 5 gates) |

Every symbol lost in fold 0, so admission correctly refused to trade anything
afterwards. **The system declining to trade is the design working**, not a bug —
but it means there is no portfolio to tune.

The 23.28% drawdown in the one fold that did trade is worth noting separately:
that is well beyond the repo's own risk appetite, and it came from four
positively-correlated USD positions moving together.

## What this means for the original goal

Consistency across symbols requires two things, in order:

1. **An edge on at least one symbol.** Diversification multiplies; it cannot
   create. Four streams of negative expectancy combine into one smoother stream
   of negative expectancy. ×1.30 of nothing is nothing.
2. **Genuinely independent streams.** The current set gives 1.68 effective bets.

Neither holds today. On this evidence, a consistently profitable multi-symbol
V15 is not achievable, and further parameter work on it would be fitting noise —
the variance ratios say there is no signal there to fit.

## The concrete path forward

Ordered by expected value, with the honest cost of each:

1. **Extend `instruments.py` with a USDJPY conversion series.** Cheapest real
   win available: it unlocks the JPY bloc and lifts effective bets from 1.68 to
   ~2.6, a ×1.24 improvement in achievable Sharpe. Requires loading USDJPY
   alongside any JPY-quoted pair and converting each trade's P&L at its exit
   time.
2. **Test a mean-reversion hypothesis instead.** Every variance ratio below 1.0
   at long horizons is weak evidence pointing the opposite way to V15. None is
   individually significant, but the direction is consistent across six symbols
   and it costs one pre-registered specification to test properly.
3. **Widen the universe beyond FX majors.** Indices, rates and commodities have
   different drivers; gold is already the only real diversifier in this set.
   This is the only route to a materially higher effective-bet count.
4. **Do not tune V15 per symbol.** Every variant raises `n_trials` — now tracked
   automatically in `research/v15_trials.json` so the count cannot quietly reset
   — and the deflated Sharpe rises with it. Tuning a strategy whose premise the
   data rejects is how twenty-five profiles happened.

## Modules added

| file | purpose |
|---|---|
| `mt5_ai_bridge/persistence.py` | Lo–MacKinlay variance ratio, Hurst; does this market trend? |
| `mt5_ai_bridge/portfolio_v15.py` | multi-symbol replay, vol targeting, exposure caps, diversification maths |
| `mt5_ai_bridge/validation.py::TrialRegistry` | persistent multiplicity count |
| `research/v15_persistence_scan.py` | per-symbol trend test |
| `research/v15_portfolio_test.py` | portfolio walk-forward with admission |

656 tests pass (610 before this work, 46 added). The variance-ratio estimator is
calibrated against AR(1) processes of known sign and strength, because an
earlier version silently collapsed every z-score toward zero.
