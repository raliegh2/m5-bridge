# V14.22 Order-Flow Shadow Validation

V14.22 measures broker-local tick pressure, spread, and depth of market (DOM)
when the broker supplies it. The measurement runs immediately before every
candidate enters the existing V14.21 executor.

Spot FX has no centralized order book. Historical DOM was not available, so
the research backtest used completed-candle direction, tick volume, body
pressure, close location, and volume ratio as non-look-ahead proxies.

The development/validation/holdout backtest did not find a stable blocking
filter:

- ICT M30 context selected no viable filter.
- V12 H4 filtering improved development drawdown but did not remain profitable
  or improve profit factor consistently out of sample.
- Gold M30 filtering worsened validation profit factor and worsened holdout
  profit factor and drawdown.

For that reason, live behavior defaults to `SHADOW_ONLY`. A `CONFLICT` sets
`hypothetical_block=true`, but does not change the actual execution result.
Every candidate and its actual result are written to
`state/v14_22_order_flow_shadow.jsonl` and displayed on the dashboard.

A `BLOCK_CONFLICT` execution capability is present, but startup validation
rejects it unless a separate forward-validation gate is explicitly marked as
passed:

```text
V14_22_ORDER_FLOW_ENFORCEMENT_MODE=BLOCK_CONFLICT
V14_22_ORDER_FLOW_FORWARD_GATE_PASSED=true
```

This lock prevents promoting a filter using the same historical data used to
design it. The gate must represent independent forward evidence.

## Forward evidence and graduated risk

Every filled position now retains its candidate-time broker tick/DOM summary.
When the position closes, net P/L is converted to R and stored in a separate
engine/timeframe bucket. Raw candidate readings remain in the JSONL shadow log.

The runtime requires at least 200 closed candidates in a bucket, then splits
them chronologically into calibration and confirmation halves. `REDUCE_CONFLICT`
or `BLOCK_CONFLICT` can act only when the proposed treatment improves all three
metrics in both halves:

- net R;
- profit factor;
- maximum drawdown in R.

Each half must also contain at least ten conflict observations. Insufficient or
failed evidence preserves the original engine signal and risk.

The lower-risk mode is configured as follows, but remains inactive by default:

```text
V14_22_ORDER_FLOW_ENFORCEMENT_MODE=REDUCE_CONFLICT
V14_22_ORDER_FLOW_FORWARD_GATE_PASSED=true
V14_22_ORDER_FLOW_MINIMUM_CLOSED_CANDIDATES=200
V14_22_ORDER_FLOW_MINIMUM_CONFLICTS_PER_PARTITION=10
V14_22_ORDER_FLOW_CONFLICT_RISK_MULTIPLIER=0.50
```

The dashboard exposes each bucket's sample count, status and eligibility so
collection and failed gates cannot remain silent.

The generated backtest report is at
`research/v14_22_order_flow_filter_out/V14_22_ORDER_FLOW_FILTER_REPORT.md`.

Configuration:

```text
V14_22_ORDER_FLOW_SHADOW=true
V14_22_ORDER_FLOW_SHADOW_LOG_PATH=state/v14_22_order_flow_shadow.jsonl
V14_22_ORDER_FLOW_DIRECTIONAL_THRESHOLD=0.15
V14_22_ORDER_FLOW_MINIMUM_TICKS=30
```

Do not promote the filter to blocking based on development results. Promotion
requires sufficient forward candidates and improved profit factor and drawdown
on unseen outcomes.

The order-flow measurement is universal: every candidate from every existing
engine reaches it inside the shared executor immediately before the normal
execution controls. It is not a standalone strategy. Directional tick/DOM,
signed-volume, body-pressure, and close-location blockers remain non-blocking
because they weakened untouched holdout performance.
