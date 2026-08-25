# US_INDEX_FORWARD_V1

## Objective

Create a new automatic **demo forward-test** candidate for US index exposure, using a learned model whose coefficients are fit on at least five years of historical data and then frozen before any later evaluation period is scored.

This branch is intentionally not a funded-live deployment. It is the bridge between historical research and a genuine forward test.

## Research asset and execution scope

- Research symbol: `US500` daily history already committed in `research/data/US500_D1.csv`.
- Training cutoff: **2020-12-31 UTC**.
- The committed series begins in 2012, so the training slice is materially longer than the required five years.
- All rows after the cutoff are validation/forward-simulation only; they are never used to refit the model.
- Live execution symbol is configurable with `US_INDEX_FORWARD_EXEC_SYMBOL` so a broker-specific US500/ES/MES-style symbol can be mapped after its contract economics are verified.
- **Options are disabled in V1.** The repository does not contain five years of historical option-chain data, so claiming an options model is trained would be false.

## Model

The learner is ridge regression implemented with NumPy. It predicts the next five completed daily bars' return from six backward-looking features:

1. 1-day return
2. 5-day return
3. 20-day return
4. 20-day z-score
5. 20-day realised volatility
6. ATR / price

The signal threshold is the 70th percentile of the absolute **training-only** predictions. Predictions above it are BUY, below the negative threshold are SELL, and everything else is FLAT.

The model is trained exactly once into `research/us_index_forward_v1_model.json`. The live runner only loads that artifact; it never retrains itself.

## Leakage control

A training row is eligible only when both:

- the feature timestamp is on or before `2020-12-31`, and
- the five-bar forward target timestamp is also on or before `2020-12-31`.

The regression tests deliberately multiply every post-cutoff price by 50 and assert that the trained means, standard deviations, coefficients, intercept, and signal threshold are unchanged.

## Risk envelope

The model carries forward the current tactical book's risk philosophy rather than inventing a more aggressive one:

- 0.50% account risk at the initial stop per trade
- 70% maximum allocation/notional cap in the research sizing model
- 5% drawdown: begin tapering exposure
- 20% drawdown: no new risk
- 25% minimum governor multiplier before the hard stop
- 2% intraday account-loss stop in the live runner
- one managed position at a time
- 2.5 ATR initial stop
- 3.5 ATR take-profit
- five completed D1 bars maximum holding period

AUTO execution is hard-blocked unless MT5 explicitly reports a demo account.

## Historical gate before forward testing

`research/train_us_index_forward_v1.py` fits the artifact and evaluates only the post-cutoff data. The automatic runner refuses to start if the historical result does not meet all of these minimum forward-test gates:

- at least 5.0 training years
- positive post-cutoff return
- post-cutoff profit factor >= 1.0
- post-cutoff max drawdown <= 10%
- at least 40 post-cutoff trades

Passing these gates means only **worth demo forward-testing**. It is not evidence sufficient for funded/live trading.

## Reproducible workflow

```bash
python research/train_us_index_forward_v1.py
pytest -q tests/test_us_index_forward_v1.py
```

Then inspect:

- `research/us_index_forward_v1_model.json`
- `research/us_index_forward_v1_result.json`

For a dry live decision:

```bash
set US_INDEX_FORWARD_ENABLED=true
set MODE=READ_ONLY
python us_index_forward_bot.py --dry-run
```

Only after the historical gate passes and the broker symbol specification is verified should the same runner be moved through `APPROVAL` and then `AUTO` on a demo account.

## Futures/options follow-up

The next derivatives-specific step should use actual exchange/broker futures history and historical option chains, not silently substitute spot/index data. In particular:

- verify ES/MES contract multiplier, tick size/value, margin, trading hours and symbol roll behavior;
- train/validate on continuous futures with roll handling;
- add options only after five-plus years of chain data with strike, expiry, IV, Greeks, bid/ask and corporate/calendar alignment are available;
- keep futures/options results separated from the US500 proxy result so basis and execution differences are measurable.
