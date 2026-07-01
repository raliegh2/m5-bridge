# Architecture

## Pipeline

```
MT5 -> Indicators -> Strategy -> Risk -> Plan -> (Approval) -> Execute -> Journal
```

Each loop iteration in `app.run()` walks this pipeline once (`_run_once`), then
sleeps for `LOOP_INTERVAL_SECONDS`. The `Journal` feeds the dashboard and
offline analysis.

## The client boundary

All interaction with the `MetaTrader5` package lives in `mt5_client.py`.
`RealMT5Client` wraps the library (lazy import) and re-exports the constants the
rest of the code needs. Every other module takes a `client` argument with that
small surface — `RealMT5Client` in production, `FakeMT5Client` in tests — which
is what makes the system verifiable without a broker.

## The strategy seam (direction)

`strategy_fn(market) -> Decision` produces the trade direction and a confidence.
`strategy.evaluate_strategy` (trend rule) and `reasoning.ReasoningStrategy`
(confluence scoring + RSI veto) are interchangeable; `app.make_strategy` selects
from `STRATEGY`. The same seam is used by the backtester.

## The planner (sizing, sessions, style)

`planner.build_plan(decision, now, session, sizing, style)` turns a direction
into a concrete `TradePlan`:

- **Direction** = the signal (BUY long / SELL short).
- **Size** = `LOT_SIZE`, multiplied by `NY_SIZE_MULTIPLIER` when `now` is inside
  the NY session window (`NY_START_HOUR..NY_END_HOUR`, UTC).
- **Style** rotates by trend strength: `confidence >= SWING_CONFIDENCE` -> swing
  (`SWING_SL/TP_PIPS`, wider); otherwise intraday (`INTRADAY_SL/TP_PIPS`,
  tighter).

The planner is pure and unit-tested; `app.make_planner_configs` builds its
config dataclasses from `Settings`.

## Execution & throttling

In `_consider_trade`, once a signal passes risk:

- The **daily cap** (`journal.count_trades_today()` vs `MAX_TRADES_PER_DAY`)
  stops opening new trades past the quota; it is journal-backed so it survives
  restarts.
- A **same-direction guard** (`_has_open_side`) prevents stacking an identical
  position every loop.
- `_execute_plan` places the order. In APPROVAL it prompts; in AUTO it places
  directly. Exits are handled by the SL/TP attached to each order, so AUTO needs
  no manual close step.

`execution.place_market_order` coerces an `OrderSide`, a `Signal`, or a string,
computes price/SL/TP from pip size, and submits via the client.

## Risk & resilience

- `check_risk(account, positions, limits, daily_loss)` blocks on total loss,
  daily loss, or max open positions.
- `DailyLossTracker` reports drawdown from the day's starting equity.
- The loop catches per-iteration failures, reconnects with backoff, and gives up
  after `RECONNECT_ATTEMPTS` consecutive failures.

## Persistence & dashboard

`journal.py` is append-only SQLite (`signals`, `orders`, `risk_events`).
`dashboard.py` renders a static, dependency-free HTML view (cards, inline SVG
equity curve, tables).

## Behaviour change vs. the original

The original risk engine checked the daily limit before the total limit, making
the total branch unreachable (250 < 500). The refactor checks total first.
