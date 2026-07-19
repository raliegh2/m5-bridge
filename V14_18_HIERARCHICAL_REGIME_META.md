# V14.18 Hierarchical Regime Meta-Labeler

V14.18 is stacked on `v14-17-cost-adjusted-consensus`. It adds a leakage-safe market-regime classifier and hierarchical meta-label layer without changing signals, exits, transaction-cost assumptions, or portfolio risk ceilings.

## Market regimes

Every candidate is classified before entry:

- **TREND:** directional V12 breakout, pullback, precision, or swing-core candidates.
- **RANGE:** ICT liquidity and session-sweep candidates.
- **TRANSITION:** retest/fade candidates or same-symbol V12–ICT conflict.
- **DISLOCATED:** inherited defensive market state.

The first classifier is structural and uses only fields already known at signal time. Price-derived regime features are deferred until historical/live feed parity is available.

## Hierarchical evidence

Broker-net R evidence is updated only after an executed trade closes. The hierarchy is:

1. Global
2. Mode
3. Market regime
4. Symbol/mode
5. Engine/regime
6. Setup/regime
7. Direction/regime
8. Session/regime
9. Hour/regime

Each child estimate is shrunk toward its parent with a frozen prior strength of 24 trades. The audit ledger records the posterior score, confidence, effective sample size, node statistics, assigned regime, and final meta-label.

## Initial stable policy

- **FULL:** preserve inherited risk.
- **REDUCED:** use 50% of inherited risk.
- **OBSERVATION:** use 25% of inherited risk.
- **SHADOW:** retain the candidate and modeled outcome for diagnostics but send no broker order.

V14.18 cannot increase risk. A non-FULL label is allowed only when:

- transaction cost is nonzero;
- V14.17 already assigned `REASONING_REDUCED`;
- the candidate is V12 in TREND or TRANSITION;
- mature engine/direction evidence remains materially negative;
- the hierarchy does not provide an exceptionally strong positive override.

ICT/RANGE candidates are classified and audited but remain FULL during the stability phase.

## Live boundary

Live action requires explicitly chronological, broker-reconciled evidence with at least:

- 40 direction trades;
- 50 engine trades;
- 60 symbol/mode trades.

Missing or immature evidence preserves V14.17 risk. Live V14.18 cannot create an uplift.

## Range mean-reversion boundary

The new range mean-reversion engine is **not implemented in V14.18**. It remains deferred until this meta-label package is stable. Its future first release must be shadow-only and require:

- at least 100 closed shadow trades;
- positive net expectancy under retail and stress costs;
- positive performance across multiple chronological windows;
- no material deterioration of portfolio drawdown or existing engine coverage.

## Retained controls

- 0.80% maximum single-trade risk.
- 1.75% maximum ICT open risk.
- 3.25% maximum combined open risk.
- 9.40% projected-stress admission buffer.
- Existing drawdown, loss-pressure, cost, staleness, reconciliation, and demo-only controls.

Historical modeled performance does not guarantee future demo or live returns.
