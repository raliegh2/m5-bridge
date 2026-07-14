"""Research-only V13 profile combining V12 Final controls with V11 intraday signals.

This profile now includes the forward-test correction for GBPJPY loss clusters:
one open GBPJPY position maximum, 0.20% normal risk, 0.10% post-loss risk,
two-loss daily shutdown, and a persistent four-hour rolling-loss cooldown.
Broker execution remains disabled at the profile level; the same controls are
implemented in ``gbpjpy_guarded_execution`` for the named-engine demo adapter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from .gbpjpy_guard import GBPJPYGuardConfig
from .v12_final_risk import BacktestExactLimits, EngineRule


PROFILE_ID = "V13_V12_FINAL_PLUS_V11_INTRADAY_GBPJPY_GUARDED"
BASE_PROFILE_ID = "V12_FINAL_3201_58"
MODE = "READ_ONLY"
ALLOW_BROKER_ORDER_API = False
REQUIRE_HUMAN_REVIEW = True


V11_INTRADAY_ENGINE_RULES: Mapping[str, EngineRule] = {
    "GBPUSD_V11_INTRADAY": EngineRule(
        symbol="GBPUSD",
        allowed_risk_percent=(0.30, 0.35, 0.40),
        allowed_setups=("GBPUSD_SATELLITE_V3_INTRADAY",),
    ),
    "EURUSD_V11_INTRADAY": EngineRule(
        symbol="EURUSD",
        allowed_risk_percent=(0.30, 0.35, 0.40),
        allowed_setups=("EURUSD_SATELLITE_V7_INTRADAY",),
    ),
    "GBPJPY_V11_INTRADAY": EngineRule(
        symbol="GBPJPY",
        allowed_risk_percent=(0.10, 0.20),
        allowed_setups=("GBPJPY_SATELLITE_V7_INTRADAY",),
        adaptive=True,
    ),
}


@dataclass(frozen=True)
class V13CombinedProfile:
    """Combined research profile using V12 limits and guarded V11 proposals."""

    profile_id: str = PROFILE_ID
    base_profile_id: str = BASE_PROFILE_ID
    mode: str = MODE
    v12_final_controls_imported: bool = True
    v11_intraday_added: bool = True
    intraday_only_for_v11: bool = True
    allow_v11_overnight_positions: bool = False
    force_v11_flat_hour_utc: int = 20
    allow_broker_order_api: bool = ALLOW_BROKER_ORDER_API
    require_human_review: bool = REQUIRE_HUMAN_REVIEW
    exact_limits: BacktestExactLimits = BacktestExactLimits()
    gbpjpy_guard: GBPJPYGuardConfig = GBPJPYGuardConfig()

    def validate(self) -> None:
        if self.mode != "READ_ONLY":
            raise ValueError("V13 combined profile must remain READ_ONLY")
        if self.allow_broker_order_api:
            raise ValueError("V13 combined research profile cannot call broker order API")
        if not self.require_human_review:
            raise ValueError("V13 combined profile requires explicit human review")
        if not self.v12_final_controls_imported:
            raise ValueError("V12 Final controls must remain the master risk governor")
        if not self.v11_intraday_added:
            raise ValueError("V11 intraday engines must be present for this profile")
        if not self.intraday_only_for_v11:
            raise ValueError("V11 side must remain intraday-only")
        if self.allow_v11_overnight_positions:
            raise ValueError("V11 intraday side cannot hold overnight")
        if not 0 <= self.force_v11_flat_hour_utc <= 23:
            raise ValueError("force_v11_flat_hour_utc must be a UTC hour")
        for engine in V11_INTRADAY_ENGINE_RULES:
            if "SWING" in engine.upper():
                raise ValueError(
                    f"V11 intraday extension cannot include swing engine: {engine}"
                )
        self.gbpjpy_guard.validate()
        gbpjpy = V11_INTRADAY_ENGINE_RULES["GBPJPY_V11_INTRADAY"]
        expected = {
            self.gbpjpy_guard.post_loss_risk_cap_percent,
            self.gbpjpy_guard.normal_risk_cap_percent,
        }
        if set(gbpjpy.allowed_risk_percent) != expected:
            raise ValueError("GBPJPY engine risk tiers must match the guard caps")

    def summary(self) -> dict:
        self.validate()
        return {
            "profile_id": self.profile_id,
            "base_profile_id": self.base_profile_id,
            "mode": self.mode,
            "v12_limits": asdict(self.exact_limits),
            "v11_intraday_engines": {
                engine: asdict(rule)
                for engine, rule in V11_INTRADAY_ENGINE_RULES.items()
            },
            "gbpjpy_guard": asdict(self.gbpjpy_guard),
            "execution_boundary": {
                "broker_order_api": self.allow_broker_order_api,
                "human_review_required": self.require_human_review,
                "v11_force_flat_hour_utc": self.force_v11_flat_hour_utc,
                "v11_allow_overnight": self.allow_v11_overnight_positions,
            },
        }


V13_COMBINED_PROFILE = V13CombinedProfile()
V13_COMBINED_PROFILE.validate()
