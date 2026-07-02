"""Configuration and identifiers for the V12 adaptive swing portfolio."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

ENGINE_V10_GBPUSD = "GBPUSD_V10_PRECISION"
ENGINE_GBPUSD_RETEST = "GBPUSD_SWING_RETEST"
ENGINE_EURUSD_CORE = "EURUSD_SWING_CORE"
ENGINE_EURUSD_RETEST = "EURUSD_SWING_RETEST"
ENGINE_GBPJPY_CORE = "GBPJPY_SWING_CORE"
ENGINE_GBPJPY_RETEST = "GBPJPY_SWING_RETEST"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class AdaptiveGuardConfig:
    rolling_trades: int = 12
    minimum_trades: int = 12
    full_profit_factor: float = 1.15
    full_net_r: float = 0.0
    reduced_profit_factor: float = 0.90
    reduced_net_r: float = -1.20
    reduced_multiplier: float = 0.50
    cooldown_days: int = 60
    probe_multiplier: float = 0.50
    protected_engines: tuple[str, ...] = (
        ENGINE_V10_GBPUSD,
        ENGINE_GBPUSD_RETEST,
        ENGINE_GBPJPY_RETEST,
        ENGINE_EURUSD_CORE,
        ENGINE_GBPJPY_CORE,
    )


@dataclass(frozen=True)
class V12HybridConfig:
    state_path: str = "state/v12_hybrid_adaptive_state.json"
    max_open_positions: int = 5
    max_open_risk_percent: float = 1.50
    max_symbol_risk_percent: float = 0.40
    aligned_gbp_cap_percent: float = 0.90
    mixed_gbp_cap_percent: float = 0.65
    basket_cap_enabled: bool = True
    max_positions_per_basket: int = 1
    stagger_cooldown_hours: float = 4.0
    precision_primary_a_risk: float = 0.40
    precision_primary_b_risk: float = 0.20
    precision_secondary_risk: float = 0.40
    precision_pullback_risk: float = 0.40
    guard: AdaptiveGuardConfig = field(default_factory=AdaptiveGuardConfig)

    @classmethod
    def from_env(cls) -> "V12HybridConfig":
        base = AdaptiveGuardConfig()
        protected = tuple(
            item.strip()
            for item in os.getenv(
                "V12_PROTECTED_ENGINES", ",".join(base.protected_engines)
            ).split(",")
            if item.strip()
        )
        guard = AdaptiveGuardConfig(
            rolling_trades=int(os.getenv("V12_GUARD_ROLLING_TRADES", "12")),
            minimum_trades=int(os.getenv("V12_GUARD_MINIMUM_TRADES", "12")),
            full_profit_factor=float(os.getenv("V12_GUARD_FULL_PF", "1.15")),
            full_net_r=float(os.getenv("V12_GUARD_FULL_NET_R", "0.0")),
            reduced_profit_factor=float(os.getenv("V12_GUARD_REDUCED_PF", "0.90")),
            reduced_net_r=float(os.getenv("V12_GUARD_REDUCED_NET_R", "-1.20")),
            reduced_multiplier=float(os.getenv("V12_GUARD_REDUCED_MULTIPLIER", "0.50")),
            cooldown_days=int(os.getenv("V12_GUARD_COOLDOWN_DAYS", "60")),
            probe_multiplier=float(os.getenv("V12_GUARD_PROBE_MULTIPLIER", "0.50")),
            protected_engines=protected,
        )
        config = cls(
            state_path=os.getenv("V12_STATE_PATH", cls.state_path),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "5")),
            max_open_risk_percent=float(os.getenv("MAX_OPEN_RISK_PERCENT", "1.50")),
            max_symbol_risk_percent=float(os.getenv("V12_MAX_SYMBOL_RISK_PERCENT", "0.40")),
            aligned_gbp_cap_percent=float(os.getenv("ALIGNED_GBP_RISK_CAP_PERCENT", "0.90")),
            mixed_gbp_cap_percent=float(os.getenv("MIXED_GBP_RISK_CAP_PERCENT", "0.65")),
            basket_cap_enabled=_env_bool("V12_BASKET_CAP_ENABLED", True),
            max_positions_per_basket=int(os.getenv("V12_MAX_POSITIONS_PER_BASKET", "1")),
            stagger_cooldown_hours=float(os.getenv("V12_STAGGER_COOLDOWN_HOURS", "4")),
            precision_primary_a_risk=float(os.getenv("V12_GBPUSD_PRIMARY_A_RISK", "0.40")),
            precision_primary_b_risk=float(os.getenv("V12_GBPUSD_PRIMARY_B_RISK", "0.20")),
            precision_secondary_risk=float(os.getenv("V12_GBPUSD_SECONDARY_RISK", "0.40")),
            precision_pullback_risk=float(os.getenv("V12_GBPUSD_PULLBACK_RISK", "0.40")),
            guard=guard,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be positive")
        if not 0 < self.max_open_risk_percent <= 5.0:
            raise ValueError("MAX_OPEN_RISK_PERCENT must be within (0, 5]")
        if not 0 < self.max_symbol_risk_percent <= self.max_open_risk_percent:
            raise ValueError("V12 symbol-risk cap must be within the global cap")
        if not 0 < self.mixed_gbp_cap_percent <= self.aligned_gbp_cap_percent:
            raise ValueError("Mixed GBP cap must not exceed aligned GBP cap")
        if self.aligned_gbp_cap_percent > self.max_open_risk_percent:
            raise ValueError("Aligned GBP cap cannot exceed global open-risk cap")
        if self.max_positions_per_basket != 1:
            raise ValueError("V12 currently enforces exactly one position per basket")
        if self.stagger_cooldown_hours < 0:
            raise ValueError("V12 stagger cooldown cannot be negative")
        for risk in (
            self.precision_primary_a_risk,
            self.precision_primary_b_risk,
            self.precision_secondary_risk,
            self.precision_pullback_risk,
        ):
            if not 0 < risk <= self.max_symbol_risk_percent:
                raise ValueError("V12 precision risk exceeds the per-symbol cap")
        if self.guard.minimum_trades < 1:
            raise ValueError("V12 guard minimum must be positive")
        if self.guard.rolling_trades < self.guard.minimum_trades:
            raise ValueError("V12 rolling window is smaller than its minimum")
