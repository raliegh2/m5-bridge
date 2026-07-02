# V12 Horizontal Scaling Architecture

The V12 portfolio controls are rebalanced for horizontal scaling:

```env
MAX_OPEN_RISK_PERCENT=1.50
V12_MAX_SYMBOL_RISK_PERCENT=0.40
MAX_OPEN_POSITIONS=5
ALIGNED_GBP_RISK_CAP_PERCENT=0.90
MIXED_GBP_RISK_CAP_PERCENT=0.65
V12_BASKET_CAP_ENABLED=true
V12_MAX_POSITIONS_PER_BASKET=1
V12_STAGGER_COOLDOWN_HOURS=4
MODE=READ_ONLY
```

## Thematic baskets

- Eurocentric: EURUSD, EURGBP, EURAUD, EURCHF
- Commodity block: AUDUSD, USDCAD, NZDUSD
- Safe haven: USDJPY, CHFJPY, EURCHF
- GBP pairs: classified separately and governed by the existing aligned/mixed GBP exposure caps rather than the generic basket cap

EURCHF occupies both the Eurocentric and Safe Haven themes.

## Pre-trade order

1. Reject when five positions are already active.
2. Reject when the incoming non-GBP basket already has an active position.
3. Reject when the latest active position was opened less than four hours ago.
4. Size from the lower of live balance and equity, capped at 0.40% per symbol.
5. Apply the existing global and GBP correlation risk controls.
6. In READ_ONLY, perform the full validation and broker order check, log the proposed order, and never call order_send.

The basket layer is prepared for the listed pairs, but a symbol must not be added to `SYMBOLS` until it has a separately validated signal adapter.
