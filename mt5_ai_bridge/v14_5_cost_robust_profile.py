"""V14.5 cost-robust risk reallocation profile.

Derived from the July 2026 research pass (see
research/V14_5_COST_ROBUST_RESEARCH.md and
research/v14_5_cost_robust_backtest.py):

* Promoted V12 swing engines showed stable or improving out-of-sample
  per-trade expectancy (2013-2018 vs 2019-2022) with per-trade costs of
  only ~0.02-0.03R at their ATR stop sizes.
* Demoted V12 engines flipped negative out-of-sample or never cleared
  costs; they trade at micro observation risk only.
* The GBP ICT M1 stream cannot pay its own spread costs at 5-7.5 pip
  stops under any pre-registered filter variant; it trades at observation
  risk only, feeding the V14.4 live expectancy tracker.

These values feed the live adapters; they do not modify the frozen V14.3
parity modules.
"""
from __future__ import annotations

PARITY_TRADE_RISK_CEILING_PERCENT = 0.80

V14_5_PROMOTED_RISK_PERCENT = 0.75
V14_5_OBSERVATION_RISK_PERCENT = 0.025

# V14.5.1 (July 2026): the continuous regeneration from broker data
# (research/v14_6_swing_regeneration.py) showed AUDUSD_TREND_PULLBACK
# failed the same promotion rule out-of-sample 2022-2026 (-0.09R/trade,
# PF 0.83), so it is demoted to observation. EURUSD_SWING_CORE remains
# promoted on the full out-of-sample window but was negative 2023-2026
# (-0.07R/trade); the live V14.4 expectancy tracker governs it at runtime.
PROMOTED_V12_ENGINES: frozenset[str] = frozenset(
    {
        "GBPUSD_V10_PRECISION",
        "GBPJPY_SWING_CORE",
        "EURUSD_SWING_CORE",
    }
)

DEMOTED_V12_ENGINES: frozenset[str] = frozenset(
    {
        "AUDUSD_TREND_PULLBACK",
        "GBPUSD_SWING_RETEST",
        "EURUSD_SWING_RETEST",
        "USDJPY_SAFE_HAVEN_BREAKOUT",
    }
)


def v14_5_risk_percent(engine: str, mode: str) -> float:
    """Return the V14.5 per-trade risk for an engine/mode pair."""
    if mode.upper() == "ICT":
        return V14_5_OBSERVATION_RISK_PERCENT
    if engine in PROMOTED_V12_ENGINES:
        return V14_5_PROMOTED_RISK_PERCENT
    return V14_5_OBSERVATION_RISK_PERCENT


def validate_profile() -> None:
    if V14_5_PROMOTED_RISK_PERCENT > PARITY_TRADE_RISK_CEILING_PERCENT:
        raise RuntimeError("V14.5 promoted risk must stay under the 0.80% ceiling")
    if PROMOTED_V12_ENGINES & DEMOTED_V12_ENGINES:
        raise RuntimeError("An engine cannot be both promoted and demoted")
    if V14_5_OBSERVATION_RISK_PERCENT > 0.05:
        raise RuntimeError("Observation risk must remain micro-sized")


validate_profile()
