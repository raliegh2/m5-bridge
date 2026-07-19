# V14.20 Range Anti-Consensus Integration

V14.20 is stacked directly on V14.19. It does not fund the losing D1 range mean-reversion trades. Instead, the active range shadow signal is used as a no-uplift context for the existing V12/ICT portfolio.

## Integrated loss filter

A principal candidate is shadowed only when:

1. a V14.19 range shadow position is active on the same symbol;
2. the principal direction conflicts with the range direction;
3. transaction cost is non-zero;
4. the exact principal engine has at least 10 already-closed conflict trades in its rolling 20-trade history;
5. the conflict-history mean is below 0R; and
6. conflict-history profit factor is below 0.80.

Only executed principal closes update this evidence. A V14.20-shadowed candidate cannot affect later broker-net evidence.

## Retained boundaries

- Direct range risk remains 0.00%.
- No range order is transmitted.
- Zero-cost parity remains exact.
- No candidate receives a risk uplift.
- Maximum single-trade risk remains 0.80%.
- ICT open risk remains capped at 1.75%.
- Combined open risk remains capped at 3.25%.
- The projected-stress and drawdown governors remain unchanged.
- All five symbols retain V12 and ICT coverage.

## Live boundary

The existing live runner is not changed. Future controlled-demo use additionally requires:

- range-feed parity;
- chronological broker reconciliation;
- at least 20 live engine-conflict closes;
- negative mean net R; and
- profit factor below 0.80.

Research only. Do not merge or deploy solely from historical performance.
