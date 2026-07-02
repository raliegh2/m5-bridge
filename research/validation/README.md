# GBPUSD V4 validation artifacts

This directory contains the frozen V4 validation record:

- `GBPUSD_V4_VALIDATION_REPORT.md` — full validation summary
- `frozen_parameters.json` — immutable parameter manifest and hash
- `walk_forward.csv` — purged four-year/one-year walk-forward results
- `monte_carlo_summary.csv` — bootstrap/cost-stress and permutation summary
- `official_fomc_events_2016_2026.csv` — official FOMC event subset
- `HISTORICAL_EVENT_COVERAGE.md` — event-data coverage and limitations
- `forward_test_tracker.py` — 20/30-trade forward-test gate checker
- `forward_test_template.csv` — blank forward-test log

Frozen parameter SHA-256:

`dec29542446673e043b16b20556f2a0bcaa65f096b81e5ecd71e61bbdb301e6b`

Do not modify the frozen parameters during the forward-test cohort. Any strategy
change must be versioned separately and revalidated.
