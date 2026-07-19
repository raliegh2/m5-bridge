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

## Modes

- `READ_ONLY`: validates and records proposals. No order transmission.
- `APPROVAL`: requires exact `YES` for each order and a confirmed demo account.
- `DEMO_AUTO`: automatic demo transmission. It is rejected unless all explicit
  gates, acknowledgement, expected login and expected server are configured.

No funded or real-account mode exists in V14.21.

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
7. After the controlled demo-forward gate is actually satisfied, set:

   ```dotenv
   V14_21_EXECUTION_MODE=DEMO_AUTO
   V14_21_FORWARD_GATE_PASSED=true
   V14_21_ALLOW_DEMO_AUTO=true
   V14_21_ACKNOWLEDGE_DEMO_ONLY=DEMO_ONLY
   V14_21_EXPECTED_LOGIN=<exact demo login>
   V14_21_EXPECTED_SERVER=<exact demo server>
   ```

8. Run `Start-V14-21-Demo-Auto.bat`. It performs the strict AUTO preflight
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
funded-account authorization. Keep the state file between runs so peak equity,
initial balance, reconciled losses, duplicate keys and live expectancy are not
reset.
