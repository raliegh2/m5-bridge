# V14.17 Cost-Adjusted Consensus

V14.17 is stacked on `v14-16-cost-efficient-quality-allocation`. It keeps the V14.16 signal stream, full-position exits, quality allocation ceiling, transaction-cost scenarios, drawdown governor and portfolio caps unchanged.

## Added admission controls

- **Cost-adjusted contextual expectancy:** prior closed net-R evidence is maintained for symbol/mode, engine, setup, engine/direction, UTC hour, session and parent cost regime.
- **Dynamic V12 demotion:** a V12 engine/direction sleeve is reduced to 50% of its inherited risk only after 20 prior closed trades, mean net result below -0.05R and profit factor below 0.95. The 0.025% research floor is retained.
- **V12-ICT consensus:** the latest prior signal from the other engine group on the same symbol is classified as aligned, conflicting or unavailable. Consensus never creates an uplift; conflict can only make an already-authorized demotion stricter.
- **Correlation-aware admission:** projected directional exposure in either currency is capped at 2.40% before the inherited 1.75% ICT and 3.25% combined portfolio caps.
- **No reversal of prior reductions:** V14.17 reductions use the inherited `REASONING_REDUCED` state, so V14.16 quality allocation cannot promote the candidate back to 0.80%.

## Chronology and leakage boundary

Only trades with `exit_time <= candidate entry_time` update contextual expectancy. Each cost scenario creates a fresh controller, so one scenario cannot contaminate another. Entry fields, current active positions and previously closed broker-net R results are the only inputs to the V14.17 overlay.

## Live boundary

Historical replay authorization does not authorize live risk changes. Live contextual action requires metadata explicitly marked broker reconciled, at least 30 engine/direction trades and at least 40 symbol/mode trades. Missing or immature evidence preserves the inherited V14.16 decision and never authorizes more risk.

## Exit research boundary

Exit research is design-only in V14.17. This branch does not modify stop loss, take profit, break-even, trailing, timeout, partial close or close-order behavior. A future exit experiment must be a separate shadow backtest and cannot feed the main six-scenario result.

## Retained limits

- 0.80% maximum single-trade risk.
- 1.75% maximum open ICT risk.
- 3.25% maximum combined open risk.
- 9.40% projected-stress admission buffer.
- 7.5/8.5/9.0/9.6% drawdown governor.
- READ_ONLY and controlled demo-forward only.

Historical modeled returns do not guarantee future demo or live performance.
