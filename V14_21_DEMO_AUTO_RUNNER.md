# V14.21 Demo-Only Automatic Runner

V14.21 adds an unattended **demo-account execution surface** to the validated
V14.20 model. It extends the existing reconciled V14.3/V14.4 MT5 path rather
than introducing a second order adapter.

## Execution pipeline

1. Load V12 and ICT signals from completed candles.
2. Reject direct V14.19 range-reversion orders; that family remains shadow-only.
3. Apply the V14.20 range anti-consensus live filter when reconciled,
   chronological, feed-parity evidence is attached to the candidate.
4. Confirm the terminal is connected and external trading APIs are enabled.
5. Confirm the account is MT5 demo and matches the pinned login and server.
6. Enforce the filesystem kill switch.
7. Reconcile closed positions and update broker-net state.
8. Enforce the $250 daily, $500 total and two-consecutive-loss stops.
9. Apply inherited staleness, spread-cost, live-expectancy and drawdown guards.
10. Enforce the 0.80% single-trade, 1.75% ICT and 3.25% combined-risk ceilings.
11. Size downward with broker-native `order_calc_profit`.
12. Call `order_check`.
13. In `DEMO_AUTO`, call `order_send` only after every gate above passes.
14. Persist the broker position and append a credential-free JSONL audit record.

The runner is pinned to the hashed
`V14_21_FORWARD_CANDIDATE_2026_07_03` portfolio manifest. Signals with an
unknown engine, symbol, mode or risk allocation fail closed. The existing
`GOLD_INTRADAY_M30` engine remains shadow-only because the historical Gold
daily ledger does not validate that implementation.

## Modes

- `READ_ONLY`: validates and records proposals. No order transmission.
- `APPROVAL`: requires exact `YES` for each order, a pinned demo login/server,
  the demo-only acknowledgement and a confirmed MT5 hedging account. Use this
  mode to collect real broker-forward data manually.
- `DEMO_LEARNING_AUTO`: unattended data collection on the same pinned demo
  hedging account. It does not require prior forward evidence, caps every order
  at 0.25% risk, keeps all execution/risk gates active, and writes both
  machine-readable JSONL and a readable Markdown decision journal. It cannot
  promote or modify the trading model while running.
- `DEMO_AUTO`: automatic demo transmission. It is rejected unless all explicit
  gates, acknowledgement, expected login/server and a recomputable DEMO
  forward-evidence artifact are configured.

No funded or real-account mode exists in V14.21.

To collect automatic demo learning evidence, set:

```dotenv
V14_21_EXECUTION_MODE=DEMO_LEARNING_AUTO
V14_21_ACKNOWLEDGE_DEMO_ONLY=DEMO_ONLY
V14_21_EXPECTED_LOGIN=<exact demo login>
V14_21_EXPECTED_SERVER=<exact demo server>
V14_21_REQUIRE_HEDGING_ACCOUNT=true
V14_21_LEARNING_MAX_RISK_PERCENT=0.25
V14_21_LEARNING_JSONL_PATH=state/v14_21_learning_events.jsonl
V14_21_LEARNING_DOCUMENT_PATH=state/v14_21_learning_journal.md
```

Every candidate records the engine, setup, direction, pre-entry metadata,
decision code, execution explanation, requested/executed risk and proposal.
Reconciliation later appends broker-net P/L and R-multiple close events. Model
training must occur offline against this immutable evidence; a trained
challenger remains shadow-only until it passes a new forward gate.

## Setup

1. Install the repository environment and the MetaTrader5 Python package on the
   Windows computer running the MT5 terminal.
2. Copy `.env.v14-21-demo-auto.example` to `.env`.
3. Add only demo credentials.
4. Start in `READ_ONLY`.
5. Run:

   ```powershell
   python v14_21_demo_auto_preflight.py --allow-read-only
   python v14_21_demo_auto_runner.py
   ```

6. Review proposals, broker-native sizing, dashboard state and the V14.21 audit
   log.
7. Record every accepted and rejected candidate in the schema shown by
   `research/v14_21_forward_ledger_template.csv`. Build the evidence artifact
   from the unedited source ledger:

   ```powershell
   python v14_21_forward_gate.py state/v14_21_forward_ledger.csv --phase DEMO
   ```

   The gate requires at least 56 calendar days, eight active weeks, 200 closed
   accepted trades, 10 accepted trades per active week, profit factor 1.10,
   positive net R, maximum drawdown 9.5%, and zero rule, future-data or hard-stop
   violations. The runtime re-hashes and re-evaluates the source CSV; editing
   the JSON summary cannot promote the bot.
8. After that gate actually passes, set:

   ```dotenv
   V14_21_EXECUTION_MODE=DEMO_AUTO
   V14_21_FORWARD_GATE_PASSED=true
   V14_21_ALLOW_DEMO_AUTO=true
   V14_21_ACKNOWLEDGE_DEMO_ONLY=DEMO_ONLY
   V14_21_EXPECTED_LOGIN=<exact demo login>
   V14_21_EXPECTED_SERVER=<exact demo server>
   V14_21_FORWARD_EVIDENCE_PATH=state/v14_21_forward_evidence.json
   ```

9. Run `Start-V14-21-Demo-Auto.bat`. It performs the strict AUTO preflight
   before starting the scheduler.

## Emergency stop

Create the configured kill-switch file:

```powershell
New-Item -ItemType File state\V14_21_STOP
```

Every new candidate is rejected while the file exists. Existing broker
positions remain broker-managed by their attached stop-loss and take-profit;
the runner does not silently liquidate them.

Remove the file only after the cause of the stop has been reviewed:

```powershell
Remove-Item state\V14_21_STOP
```

## V14.20 live evidence payload

A candidate may include this metadata:

```python
signal.metadata["v14_20_range_anti_consensus"] = {
    "broker_reconciled": True,
    "chronological": True,
    "range_feed_parity": True,
    "relation": "CONFLICT",
    "trades": 20,
    "mean_r": -0.20,
    "profit_factor": 0.60,
}
```

When the strict V14.20 live gate passes, V14.21 records
`V14_20_RANGE_CONFLICT_SHADOW` and sends no order.

## Important boundary

Historical results do not guarantee demo performance. `DEMO_AUTO` is not a
funded-account authorization. A real-account executor must not be added until a
second, non-overlapping locked demo window passes the same gate and the broker
execution audit receives a separate human review. Keep the state file between
runs so peak equity, initial balance, reconciled losses, duplicate keys and live
expectancy are not reset.
