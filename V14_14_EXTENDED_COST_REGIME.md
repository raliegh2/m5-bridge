# V14.14 Extended Live Cost Regime

V14.14 applies the transaction-cost decision layer to both V12 and ICT proposals.

## Engine-specific ceilings

- Robust V12 engines: up to 0.10R.
- EURUSD and AUDUSD ICT satellites: up to 0.23R.
- Strict GBPUSD and GBPJPY ICT profiles: up to 0.28R.
- USDJPY, weak V12 engines and unsupported high-cost setups: lower validated limits or shadow-only.

The policy rejects a proposal if its modeled cost consumes more than 22.5% of the planned target. It never raises the strategy's frozen risk percentage and retains all inherited exposure, loss, expectancy, staleness, spread and drawdown controls.

## Added validation scenarios

- Severe cost: 0.08R V12 and 0.23R ICT.
- Extreme cost: 0.10R V12 and 0.28R ICT.

The branch remains research/demo-forward only until the GitHub Actions replay and broker-specific forward validation pass.
