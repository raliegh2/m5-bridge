# Live session risk guard

The live `bridge.py` entrypoint wraps the MT5 client with one account-level
circuit breaker. The Gold/XAUUSD intraday engine and the H4/D1 swing engine keep
their existing entry logic, magic numbers, ATR stops, risk sizing, regime
router, factor caps, and combined-risk ceiling. The guard sits below both
engines and checks every new order before it reaches MT5.

Position closes and stop-loss modifications are never blocked. Only new market
or pending entries are gated.

## Default protections

These defaults are enabled without any `.env` changes:

| Setting | Default | Behaviour |
|---|---:|---|
| `SESSION_GUARD` | `true` | Enables the account-level guard. |
| `SESSION_MAX_DAILY_LOSS_PERCENT` | `1.0` | Locks new entries when equity falls 1% below the broker-day starting balance. |
| `SESSION_PROFIT_LOCK_ACTIVATION_PERCENT` | `1.0` | Activates profit protection after the session reaches +1%. |
| `SESSION_MAX_PROFIT_GIVEBACK_PERCENT` | `40` | Locks when 40% of the session peak profit is surrendered. |
| `SESSION_MAX_CONSECUTIVE_LOSSES` | `3` | Locks for the day after three completed net losing positions. |
| `SESSION_MAX_TRADES_PER_DAY` | `8` | Account-wide filled-entry cap. The lower of this and `MAX_TRADES_PER_DAY` wins. |
| `SESSION_MAX_TRADES_PER_SYMBOL_PER_DAY` | `4` | Per-symbol filled-entry cap across both engines. |
| `SESSION_MINIMUM_MINUTES_BETWEEN_ENTRIES` | `15` | Prevents rapid re-entry and same-loop over-stacking. |
| `SESSION_MAXIMUM_LOT` | `0.40` | Rejects any final order volume above 0.40 lots. |
| `SESSION_CANCEL_PENDING_ON_STOP` | `true` | Cancels this bot's pending orders when a daily lock triggers. |

For a $5,000 account, the defaults mean:

- Daily equity stop: approximately **-$50**.
- Profit protection activates at **+$50**.
- At a peak of **+$84**, a 40% giveback locks new entries near **+$50.40**.
- A requested 0.72-lot entry is rejected before it reaches MT5.

## Optional settings

```dotenv
# Master switch
SESSION_GUARD=true

# Daily equity circuit breaker
SESSION_ENABLE_DAILY_LOSS_LIMIT=true
SESSION_MAX_DAILY_LOSS_PERCENT=1.0
SESSION_CLOSE_POSITIONS_ON_DAILY_STOP=false

# Peak-profit giveback circuit breaker
SESSION_ENABLE_PROFIT_GIVEBACK_STOP=true
SESSION_PROFIT_LOCK_ACTIVATION_PERCENT=1.0
SESSION_MAX_PROFIT_GIVEBACK_PERCENT=40
SESSION_CLOSE_POSITIONS_ON_PROFIT_STOP=false

# Completed-trade loss streak
SESSION_ENABLE_CONSECUTIVE_LOSS_STOP=true
SESSION_MAX_CONSECUTIVE_LOSSES=3
SESSION_LOSS_COOLDOWN_MINUTES=60
SESSION_STOP_FOR_DAY_AFTER_LOSS_LIMIT=true

# Trade frequency and final order-volume gate
SESSION_ENABLE_TRADE_LIMIT=true
SESSION_MAX_TRADES_PER_DAY=8
SESSION_MAX_TRADES_PER_SYMBOL_PER_DAY=4
SESSION_MINIMUM_MINUTES_BETWEEN_ENTRIES=15
SESSION_MINIMUM_LOT=0.01
SESSION_MAXIMUM_LOT=0.40

# Persistence/history
SESSION_CANCEL_PENDING_ON_STOP=true
SESSION_HISTORY_SYNC_SECONDS=2
SESSION_HISTORY_LOOKBACK_DAYS=30
# SESSION_STATE_PATH=C:\path\to\session_guard_state.json
```

## How losses are counted

The guard reads MT5 deal history and filters it to the bridge's legacy,
intraday, and swing magic numbers. A completed position's net result includes:

- profit,
- commission,
- swap,
- trading fees.

Deals are grouped by MT5 `position_id`. Partial closes are accumulated but do
not increment the loss streak until the position is fully closed. A genuinely
profitable completed position resets the consecutive-loss counter. Manual
trades and other EAs are ignored unless they use one of this bridge's magic
numbers.

## Persistence and visibility

The default state file is derived from `DB_PATH`, for example:

```text
journal.db  ->  journal_session_guard.json
```

It stores the broker day, starting balance/equity, peak profit, current
giveback, trade counts, loss streak, cooldown, lock reason, and already-processed
position IDs. The state survives bot and terminal restarts and resets only when
a new broker day is observed from MT5 tick timestamps.

Lock activations, completed losses, protective actions, and entry rejections
are written to the normal bridge log. They are also inserted into the existing
`risk_events` journal used by the dashboard.

## Important interaction with the engines

The existing position sizing is fixed-fractional and ATR-based. It does not
contain martingale or loss-multiplier logic. Different lot sizes can still occur
because each symbol/engine has a different configured risk percentage, pip
value, and stop distance. `SESSION_MAXIMUM_LOT` is an independent final safety
ceiling applied after those calculations.

Because the 15-minute interval is account-wide, a swing entry can temporarily
block an immediate intraday add, and vice versa. Set
`SESSION_MINIMUM_MINUTES_BETWEEN_ENTRIES=0` only after testing demonstrates that
same-signal stacking is intentional and safe.

## Validation

Run the complete suite before deployment:

```bash
python -m pytest -q
```

Then run on a demo account and deliberately test:

1. Equity falling through the daily-loss threshold.
2. A profitable session giving back 40% of its peak.
3. Three net losing completed positions.
4. More than four entries on one symbol.
5. Two entry attempts inside 15 minutes.
6. An order above the maximum lot.
7. Restarting the bot after a daily lock.
8. Closing/trailing an existing position while new entries are locked.

Do not deploy this branch to a funded or live account until those checks pass
against the broker's exact symbols, execution model, and deal-history fields.
