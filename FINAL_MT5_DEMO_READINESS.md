# Final MT5 Demo Readiness Gate

V16 stays in `MODE=READ_ONLY` during research and shadow reconciliation.

When the model is declared final, complete these gates before enabling demo orders:

1. Run Python compilation and the complete regression suite.
2. Confirm only one bridge process is active.
3. Confirm terminal connection, account access, API permission, all five symbols, fresh ticks and valid volume steps.
4. Confirm broker suffix resolution for GBPUSD, EURUSD, GBPJPY, AUDUSD and USDJPY.
5. Reconcile at least 30–50 shadow signals against MT5 for signal time, side, stop distance, lot size, basket decision and guard state.
6. Run broker order validation for every proposed request and fail closed on stale ticks, excessive spread, invalid stops, insufficient margin or rejection.
7. Record cycle duration and broker response duration. A stale or late signal is skipped rather than queued.
8. Preserve broker-side stop losses, daily and total loss locks, persisted state and a manual kill switch.
9. Verify restart recovery and duplicate-signal prevention before enabling demo execution.

Zero broker latency or zero operational issues cannot be guaranteed. The release target is fail-closed behavior, deterministic sizing, idempotent signals, fresh data and successful demo reconciliation.