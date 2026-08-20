# What can this account actually trade — and does the index result survive?

Date: 2026-08-16

Two questions, both answered by measurement against the live terminal.

## 1. Tradability census

`trade_mode` on every symbol the broker offers, read directly from MT5:

| group | total | fully tradable | disabled |
|---|---:|---:|---:|
| Forex | 126 | **126** | 0 |
| Metals | 10 | **7** | 3 |
| **Indexes** | **26** | **0** | **26** |
| Nasdaq (equities/ETFs) | 12,363 | **11,439** | 924 |

**Every one of the 26 index CFDs is `trade_mode = DISABLED`** — USTEC, US500,
US30, US2000, DE40, UK100, JPN225, all of them. They quote, they serve history,
and they cannot be ordered on this account.

So the USTEC result from `research/INDICES_RESULT.md` — already suspect because
it failed to replicate on the longer-history indices — **is not actionable
here regardless.** A live broker account would very likely enable them; this
MetaQuotes demo does not.

Some other practical figures worth knowing:

| | |
|---|---|
| account balance | **$4,802.43**, not the $10,000 assumed in backtests |
| leverage | 1:100 |
| index min lot | 0.10 (US2000 and JPN225: 1.00) vs 0.01 on FX |
| margin, 1 min-lot | USTEC $15.05, US30 $26.83, JPN225 $432.21 |

The index ETFs QQQ, SPY and DIA are **also disabled**. But several equivalents
are not:

| ETF | tracks | mode | history |
|---|---|---|---|
| **ONEQ** | Nasdaq Composite | **FULL** | 22.9y |
| **IVV** | S&P 500 | **FULL** | 22.9y |
| **IWM** | Russell 2000 | **FULL** | 22.9y |
| **VTI** | Total US market | **FULL** | 22.9y |
| **TQQQ** | Nasdaq 100, 3x | **FULL** | 16.5y |
| **EEM** | Emerging markets | **FULL** | 22.9y |

That is both a tradable route to index exposure *and* the long history that
USTEC lacked — 22.9 years against 4.1. So the open question could be settled
properly.

## 2. Does the index result survive on 22.9 years?

All six ETFs audit clean. Variance ratios show the same mean-reverting tilt,
though weaker than the short USTEC window suggested:

| symbol | VR q=6 | VR q=30 |
|---|---:|---:|
| IVV | 0.825* | 0.749 |
| EEM | 0.906* | 0.890 |
| **ONEQ** | **0.964\*** | **0.963** |
| USTEC (4.1y, for contrast) | 0.854 | **0.822** |

Nasdaq's reversion over 22.9 years is 0.963. Over USTEC's four-year window it
measured 0.822. The strong reversion was a property of the window, not the
market.

V16 run on the ETFs, D1, walk-forward, tight costs, looked excellent:

| symbol | trades | OOS net | OOS PF | folds+ |
|---|---:|---:|---:|---:|
| TQQQ | 236 | +1,818.93 | **1.512** | 80% |
| ONEQ | 315 | +1,625.95 | 1.301 | 60% |
| IVV | 305 | +1,351.86 | 1.281 | 80% |
| VTI | 304 | +672.98 | 1.135 | 60% |
| EEM | 321 | +689.25 | 1.139 | 60% |
| IWM | 318 | +677.10 | 1.124 | 80% |

**Six of six profitable, every one clearing the 1.10 profit-factor gate.** On
its face the best result in this entire investigation.

## It is beta, not edge

Equity ETFs rise. A rule that buys dips in a rising asset earns a fine profit
factor while adding nothing. Two checks separate the cases, and it fails both.

| symbol | strategy return | **buy & hold** | long share of P&L |
|---|---:|---:|---:|
| IVV | +16.4% | **+666.9%** | 90% |
| VTI | +10.8% | +295.2% | **105%** (shorts lost money) |
| IWM | +8.8% | +204.2% | 23% |
| ONEQ | +12.3% | +44.0% | 84% |
| TQQQ | +13.7% | −7.5% | 88% |
| EEM | +2.3% | −51.8% | 33% |

* **Beaten by simply holding on 4 of 6**, and on IVV by a factor of forty.
* **84–105% of the profit comes from the long side** on the trending assets.
  VTI's short side actually lost money — the definition of drift capture.
* It "beats hold" only on the two assets where **holding lost money** (TQQQ,
  wrecked by leverage decay; EEM, down 52%). And there it returned 13.7% over
  16.5 years and 2.3% over 22.9 — roughly 0.1–0.8% a year.

The profit factor is real. It is being earned by being long a rising asset,
which a buy-and-hold investor does better with one trade and no spread.

This is why a benchmark matters. Zero is not the benchmark for a long-biased
equity strategy; holding the thing is. Judged against zero, six of six pass.
Judged against holding, four of six destroy value.

## Where that leaves the answer

**Tradable on this account:** 126 FX pairs, 7 metals, 11,439 US equities and
ETFs.

**Not tradable:** all 26 index CFDs, including USTEC — the one instrument that
came closest to clearing the gates.

**Tested and rejected:** the ETF route, which is tradable and has the long
history, produces beta rather than edge.

So the position is unchanged in substance: nothing measured on this account
clears the bar on evidence that would survive a benchmark. What *has* changed
is that the tradable universe is far larger than the six symbols this
investigation started with — 11,439 individual equities remain entirely
untested, and cross-sectional strategies on single stocks are a genuinely
different problem from anything tried here.

## Files

| file | purpose |
|---|---|
| `research/etf_beta_check.py` | long/short split and buy-and-hold benchmark |
| `research/etf_beta.json` | the table above |
| `research/etf_levers.json` | V16 on ETFs across cost tiers |
| `mt5_ai_bridge/instruments.py` | ETF contract specs, index CFD specs |

856 tests pass.
