# V14.3 Forward-Test Protocol

Status: forward-validation protocol for the locked V14.3 edge-filtered ICT satellite.

## Purpose

The historical V14.3 result reached the target in research replay, but the edge filter was selected after reviewing historical behavior. The next validation step is to lock the rules and test them on fresh unseen data.

## Forward-test start

The uploaded historical data ends on 2026-07-03. Forward testing should begin after that cutoff, preferably from 2026-07-06 onward so the system is tested on data not used to design the filter.

## Mode

Run in shadow or paper mode first.

- V12 Final remains the master engine.
- ICT V14.3 logs every signal, accepted trade, rejected trade, risk decision, and account governor decision.
- No broker execution should be enabled until the locked forward criteria are met.

## Required log fields

Each ICT event must log:

- timestamp;
- symbol;
- setup family;
- side;
- entry price;
- stop price;
- target price;
- entry hour;
- weekday;
- accepted/rejected status;
- rejection reason;
- pre-trade equity;
- pre-trade drawdown;
- pre-trade open risk;
- assigned risk percent;
- position size;
- exit timestamp;
- exit price;
- R multiple;
- PnL;
- post-trade equity;
- post-trade drawdown.

## Pass/fail gates

Evaluate after at least 8 trading weeks or at least 200 accepted ICT trades, whichever comes later.

| Gate | Pass threshold |
|---|---:|
| Profit factor | >= 1.10 |
| Net R | > 0 |
| Max combined drawdown proxy | <= 9.50% |
| Average weekly accepted ICT trades | >= 10 |
| Rule violations | 0 |
| Future-data usage | 0 |
| Hard-stop breaches | 0 |

## Promotion rule

The module may move from shadow mode to limited paper execution only if all gates pass. It may move from limited paper execution to small live execution only after a second locked validation window also passes.

## Failure rule

If the forward test fails, do not tune the same period immediately. Freeze the failed result, document the failure, and define a new walk-forward selection protocol using a separate training window and a separate untouched test window.
