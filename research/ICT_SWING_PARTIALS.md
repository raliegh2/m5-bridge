# ICT, swing, partial profits, and a 10% risk ceiling

Date: 2026-08-16

Two requests: get drawdown under 10%, and raise profit using the ICT intraday
and swing engines with partial profit taking. One of those is delivered; the
other could not be, and this documents why with measurements rather than
opinion.

## The 10% ceiling — delivered

`risk_v18.CONSERVATIVE_10PCT` is a coherent profile, not a single threshold:

| control | conservative | previous (balanced) |
|---|---:|---:|
| **max drawdown (latching)** | **10.0%** | 20.0% |
| risk per trade | 0.75% | 2.0% |
| Kelly fraction used | 1/8 | 1/4 |
| aggregate open risk | 2.0% | 6.0% |
| per-currency cap | 1.5% | 4.0% |
| concurrent positions | 3 | 5 |
| daily loss kill | 1.0% | 2.0% |
| consecutive losses | 3 | 5 |
| taper begins | 3% drawdown | 5% |
| taper floor | 20% of size | 25% |

Everything is scaled so 10% is *hard to reach*, not merely forbidden at the
boundary. The ceiling is proven by test, not asserted: `test_risk_profiles.py`
drives the engine through 400 consecutive maximum-size stop-outs — the exact
case the limit exists for — and asserts the account never loses more than
10.5%. It holds.

Partial exits are implemented in `mt5_ai_bridge/partial_exits.py`, walking the
real bar path, with cost charged **per filled leg** because each scale-out
crosses the spread again.

## The profit side — measured, and it does not work

### The ICT engine, out of sample, with costs

This is the first run that charges costs on the repo's own ICT engine. Every
prior ICT report scored pure R multiples;
`research/v14_4_cost_stress_report.py` warned the edge would not survive a
pip. Profile chosen on the first 60% of history, scored on the last 40%:

| symbol | profile | plan | trades | expectancy R | PF | win% | return | max DD |
|---|---|---|---:|---:|---:|---:|---:|---:|
| EURUSD | eu_ny_relaxed | flat | 571 | −0.083 | 0.866 | 38.7% | −21.79% | 25.32% |
| EURUSD | eu_ny_relaxed | half@1R+BE | 571 | −0.073 | 0.859 | 49.0% | −19.27% | 22.62% |
| AUDUSD | au_london_15 | flat | 213 | −0.136 | 0.793 | 36.6% | −13.83% | 16.39% |
| AUDUSD | au_london_15 | half@1R+BE | 213 | −0.184 | 0.687 | 43.7% | −18.00% | 20.15% |
| USDJPY | uj_london_15 | flat | 189 | −0.078 | 0.878 | 38.1% | −7.40% | 8.35% |
| USDJPY | uj_london_15 | half@1R+BE | 189 | −0.120 | 0.782 | 46.0% | −10.95% | 11.66% |

**0 of 6 profitable.**

### It is not a cost problem

At **zero** cost, out of sample:

| symbol | expectancy R | PF | win rate |
|---|---:|---:|---:|
| EURUSD | −0.045 | 0.924 | 38.7% |
| AUDUSD | −0.079 | 0.873 | 37.1% |
| USDJPY | −0.039 | 0.935 | 38.1% |

**The ICT signal has negative expectancy before any cost is charged.** The
arithmetic is simple: a 1.5R target needs a win rate above 1/(1+1.5) = 40% to
break even, and these engines run 37–39%. They sit just under the line, and
costs push them clearly under it.

This matters for the repository's history. The V14.3 reports credited this
engine with a ~$13k result. That figure came from selecting a profile on the
same history it was scored on. Chosen on training data and scored on the
following slice, the engine loses money — before costs.

### Partial profits did not rescue it

Partials improved return/drawdown on **1 of 3** symbols:

| symbol | expectancy (flat → partial) | drawdown (flat → partial) |
|---|---|---|
| EURUSD | −0.083 → −0.073 | 25.32% → 22.62% |
| AUDUSD | −0.136 → −0.184 | 16.39% → 20.15% |
| USDJPY | −0.078 → −0.120 | 8.35% → 11.66% |

Partials do exactly what the theory says: win rate jumps (38.7% → 49.0% on
EURUSD) because banking at 1R converts near-misses into small wins. But
expectancy falls on two of three, and drawdown *rises* on two of three.

Two reasons, both structural:

1. **Each leg pays the spread again.** On ICT's tight stops the extra crossing
   is a meaningful fraction of R.
2. **Scaling out caps the winners that pay for the losses.** With a negative
   base expectancy, truncating the right tail makes it worse, not better.

Partial profit taking is a variance tool. It cannot turn a negative-expectancy
system positive, and on a tight stop it is not free.

## What this means for the request

Both halves of the request cannot be satisfied at once with these engines:

* **Under 10% drawdown:** delivered, enforced, and proven under an unbroken
  losing run.
* **More profit:** not available. The ICT engine is negative out of sample
  before costs; the swing and reversion engines were measured earlier at a
  frictionless profit factor of 1.01–1.08 against a 1.10 gate.

The only way to hold drawdown under 10% with these signals is to trade very
little or not at all — which is what the system now does, correctly, because
none of them clears the edge gate.

A profitable system needs a signal with positive out-of-sample expectancy
first. Risk management then decides how much of it you can safely harvest. It
cannot supply the expectancy itself, and no configuration of position sizing,
partial exits or stop placement changes the sign of a negative number.

## Where the remaining possibilities are

Ranked by evidence, not hope:

1. **A real order-flow tape.** `research/V19_ORDER_FLOW.md` showed a *weak*
   volume proxy halved the gap to the gate (frictionless PF 1.057 → 1.078).
   CME FX futures (6E/6B/6A) carry signed volume and aggressor side;
   `databento` is already in `requirements.txt`. This is the only avenue where
   a measured effect improved when better information was added.
2. **Fixing ICT's win rate, not its exits.** The engines are 1–3 points of win
   rate below break-even. That gap is in the *entry* filter, and partials or
   sizing cannot close it. Whether it is closable is unknown and would be a new
   pre-registered test.
3. **A different universe.** Six FX symbols give 2.6 effective bets and all
   show the same small effect.

## Files

| file | purpose |
|---|---|
| `mt5_ai_bridge/risk_v18.py` | `CONSERVATIVE_10PCT` / `BALANCED_20PCT` profiles |
| `mt5_ai_bridge/partial_exits.py` | scale-out simulation, per-leg costs |
| `research/ict_cost_and_partials.py` | the ICT measurement above |
| `tests/test_risk_profiles.py` | the 10% ceiling proven under a losing run |

Also patched: `v14_3_all_symbol_ict.py` now emits `entry_price`, `stop_price`
and `risk_price` on every candidate. Without those an R multiple cannot be
converted to money, which is the mechanical reason every previous ICT report
was gross.

848 tests pass (835 before this work, 13 added).
