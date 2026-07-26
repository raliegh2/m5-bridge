# Risk Manager Agent

Unlike the Analyst, this stage is **not** an LLM call. Risk decisions are
deterministic code (`risk_engine.py`, `session_guard.py`, `prop.py`) --
the working agreement in `CLAUDE.md` requires risk functions to stay pure
and unit-tested. This file documents the rules currently enforced, so the
Analyst prompt and this stage stay consistent with each other and with
anyone reviewing the pipeline.

## Pre-trade checks (`risk_engine.check_risk`)

A new trade is rejected if any of:

- **Total loss limit**: `balance - equity >= total_max_loss` (floating loss)
- **Daily loss limit**: today's tracked loss (from `DailyLossTracker`, which
  baselines off the day's first observed equity) `>= daily_max_loss`
- **Max open positions**: current open position count `>= max_open_positions`

## Session guard (`session_guard.py`, opt-in via `SESSION_GUARD`)

Runs underneath both the intraday and swing engines and gates NEW entries
only -- closes and trailing-stop updates always go through:

- Daily loss lock (`SESSION_MAX_DAILY_LOSS_PERCENT` of account)
- Profit-giveback lock: once up `SESSION_PROFIT_LOCK_ACTIVATION_PERCENT`,
  surrendering `SESSION_MAX_PROFIT_GIVEBACK_PERCENT` of the session peak
  locks new entries
- Consecutive-loss cooldown (`SESSION_MAX_CONSECUTIVE_LOSSES`, then a
  `SESSION_LOSS_COOLDOWN_MINUTES` pause)
- Trade-count throttles: max trades/day account-wide and per symbol, plus a
  minimum gap between entries
- Min/max lot size as a final volume sanity gate

## Prop-firm mode (`prop.py`, opt-in via `PROP_FIRM`)

For running the bot inside an FTMO-style challenge: tracks max daily loss
and max total drawdown against the challenge's starting balance (or a
trailing peak), stops opening trades once the profit target is hit, and
linearly de-risks position size as any limit is approached.

## Position sizing (`sizing.py`, `planner.py`)

Fixed-fractional: lot size derives from account balance × configured risk
percent × the ATR- or fixed-pip stop distance × the symbol's tick value --
not from anything the Analyst or a model outputs. A `combined_risk_ceiling`
also caps aggregate open risk across every symbol/engine, and factor caps
(`exposure.py`) prevent several "diversified" positions from all being the
same currency bet.

## Why no LLM here

Sizing and hard limits are exactly the kind of decision that should never
depend on a model's judgment call -- they need to be reproducible, testable,
and impossible to talk out of. If you want a model to *sanity-check* a risk
decision (e.g. flag an unusual combination) that's an additive step, not a
replacement for the checks above.
