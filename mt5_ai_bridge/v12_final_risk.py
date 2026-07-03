"""Immutable risk profile for the final V12 five-symbol demo strategy.

This module ports the portfolio constraints used by the $3,201.58 research
scenario into a broker-independent pre-trade gate.  Backtest-exact controls are
kept separate from additional demo safety overlays so later configuration
cannot silently increase risk.

The gate is intentionally fail-closed: unknown symbols, engines, setups,
missing stop data, excessive actual risk, duplicate orders, and non-demo
accounts are rejected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional


PROFILE_ID = "V12_FINAL_3201_58"
ALLOWED_SYMBOLS = frozenset({"GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"})
DISABLED_ENGINES = frozenset({"GBPUSD_SWING_CORE", "GBPJPY_SWING_RETEST"})
ADAPTIVE_ENGINES = frozenset({"EURUSD_SWING_RETEST", "USDJPY_SAFE_HAVEN_BREAKOUT"})
PROTECTED_ENGINES = frozenset({
    "GBPUSD_V10_PRECISION",
    "GBPUSD_SWING_RETEST",
    "EURUSD_SWING_CORE",
    "GBPJPY_SWING_CORE",
    "AUDUSD_TREND_PULLBACK",
})


@dataclass(frozen=True)
class EngineRule:
    symbol: str
    allowed_risk_percent: tuple[float, ...]
    allowed_setups: tuple[str, ...] = ()
    adaptive: bool = False


ENGINE_RULES: Mapping[str, EngineRule] = {
    "GBPUSD_V10_PRECISION": EngineRule(
        symbol="GBPUSD",
        allowed_risk_percent=(0.20, 0.40, 0.50),
        allowed_setups=(
            "PRIMARY_16UTC_BREAKOUT",
            "SECONDARY_12UTC_BREAKOUT",
            "GBPUSD_SWING_V5_PULLBACK_ADDON",
        ),
    ),
    "GBPUSD_SWING_RETEST": EngineRule(
        symbol="GBPUSD", allowed_risk_percent=(0.15,),
        allowed_setups=("H4_BREAKOUT_RETEST",),
    ),
    "EURUSD_SWING_CORE": EngineRule(
        symbol="EURUSD", allowed_risk_percent=(0.25,),
        allowed_setups=("H4_DONCHIAN_BREAKOUT",),
    ),
    "EURUSD_SWING_RETEST": EngineRule(
        symbol="EURUSD", allowed_risk_percent=(0.10,),
        allowed_setups=("H1_BREAKOUT_RETEST",), adaptive=True,
    ),
    "GBPJPY_SWING_CORE": EngineRule(
        symbol="GBPJPY", allowed_risk_percent=(0.15,),
        allowed_setups=("H4_DONCHIAN_BREAKOUT",),
    ),
    "AUDUSD_TREND_PULLBACK": EngineRule(
        symbol="AUDUSD", allowed_risk_percent=(0.25,),
        allowed_setups=("D1_H4_EMA_PULLBACK_04_08UTC",),
    ),
    "USDJPY_SAFE_HAVEN_BREAKOUT": EngineRule(
        symbol="USDJPY", allowed_risk_percent=(0.25,),
        allowed_setups=("D1_H4_40BAR_BREAKOUT",), adaptive=True,
    ),
}


@dataclass(frozen=True)
class BacktestExactLimits:
    max_positions: int = 5
    max_open_risk_percent: float = 1.50
    precision_symbol_cap_percent: float = 0.75
    legacy_symbol_cap_percent: float = 0.75
    new_symbol_cap_percent: float = 0.25
    aligned_gbp_cap_percent: float = 0.90
    mixed_gbp_cap_percent: float = 0.65


@dataclass(frozen=True)
class DemoSafetyLimits:
    daily_drawdown_percent: float = 1.50
    total_drawdown_percent: float = 5.00
    actual_risk_rounding_tolerance_percent: float = 0.02
    duplicate_window_seconds: int = 300
    max_spread_pips: Mapping[str, float] = field(default_factory=lambda: {
        "GBPUSD": 2.0,
        "EURUSD": 2.0,
        "GBPJPY": 4.0,
        "AUDUSD": 2.5,
        "USDJPY": 2.5,
    })


@dataclass(frozen=True)
class OpenRisk:
    symbol: str
    engine: str
    side: str
    risk_percent: float


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    engine: str
    setup: str
    side: str
    requested_risk_percent: float
    guard_multiplier: float
    stop_pips: float
    volume: float
    pip_value_per_lot: float
    spread_pips: float
    order_key: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    balance: float
    equity: float
    day_start_equity: float
    peak_equity: float
    open_risk: tuple[OpenRisk, ...] = ()
    recent_order_keys: frozenset[str] = frozenset()
    is_demo_account: bool = False
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class GateDecision:
    ok: bool
    code: str
    message: str
    expected_risk_percent: float = 0.0
    actual_risk_percent: float = 0.0


def _close(a: float, b: float, tolerance: float = 1e-9) -> bool:
    return abs(float(a) - float(b)) <= tolerance


def _symbol_cap(intent: OrderIntent, limits: BacktestExactLimits) -> float:
    if intent.engine == "GBPUSD_V10_PRECISION":
        return limits.precision_symbol_cap_percent
    if intent.symbol in {"AUDUSD", "USDJPY"}:
        return limits.new_symbol_cap_percent
    return limits.legacy_symbol_cap_percent


def _expected_risk(intent: OrderIntent, rule: EngineRule) -> Optional[float]:
    if rule.adaptive:
        if intent.guard_multiplier not in (0.0, 0.35, 0.60, 1.0):
            return None
    elif not _close(intent.guard_multiplier, 1.0):
        return None

    if intent.guard_multiplier <= 0:
        return 0.0

    matches = [
        base * intent.guard_multiplier
        for base in rule.allowed_risk_percent
        if _close(intent.requested_risk_percent, base * intent.guard_multiplier)
    ]
    return matches[0] if matches else None


def calculate_actual_risk_percent(intent: OrderIntent, balance: float) -> float:
    if balance <= 0 or intent.stop_pips <= 0 or intent.volume <= 0 or intent.pip_value_per_lot <= 0:
        return float("inf")
    risk_dollars = intent.stop_pips * intent.volume * intent.pip_value_per_lot
    return risk_dollars / balance * 100.0


def validate_order(
    intent: OrderIntent,
    snapshot: PortfolioSnapshot,
    exact: BacktestExactLimits = BacktestExactLimits(),
    safety: DemoSafetyLimits = DemoSafetyLimits(),
) -> GateDecision:
    """Validate one proposed order against the final strategy profile."""
    if not snapshot.is_demo_account:
        return GateDecision(False, "DEMO_ONLY", "Final V12 profile is demo-account only.")
    if intent.symbol not in ALLOWED_SYMBOLS:
        return GateDecision(False, "SYMBOL_NOT_ALLOWED", f"{intent.symbol} is not in the five-symbol profile.")
    if intent.engine in DISABLED_ENGINES:
        return GateDecision(False, "ENGINE_DISABLED", f"{intent.engine} is disabled by the final model.")

    rule = ENGINE_RULES.get(intent.engine)
    if rule is None:
        return GateDecision(False, "ENGINE_NOT_ALLOWED", f"Unknown engine: {intent.engine}.")
    if rule.symbol != intent.symbol:
        return GateDecision(False, "ENGINE_SYMBOL_MISMATCH", "Engine and symbol do not match.")
    if rule.allowed_setups and intent.setup not in rule.allowed_setups:
        return GateDecision(False, "SETUP_NOT_ALLOWED", f"Setup {intent.setup} is not valid for {intent.engine}.")
    if intent.side.upper() not in {"BUY", "SELL"}:
        return GateDecision(False, "SIDE_INVALID", "Side must be BUY or SELL.")

    expected = _expected_risk(intent, rule)
    if expected is None:
        return GateDecision(False, "RISK_PROFILE_MISMATCH", "Requested risk or adaptive multiplier differs from the tested profile.")
    if expected <= 0:
        return GateDecision(False, "GUARD_BLOCKED", "Adaptive guard currently blocks this engine.")

    actual = calculate_actual_risk_percent(intent, snapshot.balance)
    if actual == float("inf"):
        return GateDecision(False, "RISK_DATA_INVALID", "Balance, stop, volume, or pip value is invalid.", expected, actual)
    if actual > expected + safety.actual_risk_rounding_tolerance_percent:
        return GateDecision(False, "ACTUAL_RISK_TOO_HIGH", "Broker-sized position exceeds the tested engine risk.", expected, actual)

    if snapshot.day_start_equity <= 0 or snapshot.peak_equity <= 0:
        return GateDecision(False, "EQUITY_BASELINE_MISSING", "Daily and peak-equity baselines are required.", expected, actual)
    daily_dd = max(0.0, (snapshot.day_start_equity - snapshot.equity) / snapshot.day_start_equity * 100.0)
    total_dd = max(0.0, (snapshot.peak_equity - snapshot.equity) / snapshot.peak_equity * 100.0)
    if daily_dd >= safety.daily_drawdown_percent:
        return GateDecision(False, "DAILY_STOP", "Daily drawdown safety stop reached.", expected, actual)
    if total_dd >= safety.total_drawdown_percent:
        return GateDecision(False, "TOTAL_STOP", "Total drawdown safety stop reached.", expected, actual)

    spread_limit = safety.max_spread_pips[intent.symbol]
    if intent.spread_pips < 0 or intent.spread_pips > spread_limit:
        return GateDecision(False, "SPREAD_TOO_WIDE", f"Spread exceeds {spread_limit:g} pips.", expected, actual)
    if not intent.order_key or intent.order_key in snapshot.recent_order_keys:
        return GateDecision(False, "DUPLICATE_ORDER", "Duplicate or missing order key.", expected, actual)

    if len(snapshot.open_risk) >= exact.max_positions:
        return GateDecision(False, "MAX_POSITIONS", "Five-position portfolio limit reached.", expected, actual)

    proposed_risk = intent.requested_risk_percent
    total_open = sum(item.risk_percent for item in snapshot.open_risk) + proposed_risk
    if total_open > exact.max_open_risk_percent + 1e-9:
        return GateDecision(False, "MAX_OPEN_RISK", "1.50% total open-risk ceiling would be exceeded.", expected, actual)

    symbol_open = sum(item.risk_percent for item in snapshot.open_risk if item.symbol == intent.symbol)
    symbol_cap = _symbol_cap(intent, exact)
    if symbol_open + proposed_risk > symbol_cap + 1e-9:
        return GateDecision(False, "SYMBOL_CAP", f"{intent.symbol} risk cap would be exceeded.", expected, actual)

    if intent.symbol.startswith("GBP"):
        gbp = [item for item in snapshot.open_risk if item.symbol.startswith("GBP")]
        directions = {item.side.upper() for item in gbp}
        directions.add(intent.side.upper())
        cap = exact.mixed_gbp_cap_percent if len(directions) > 1 else exact.aligned_gbp_cap_percent
        gbp_risk = sum(item.risk_percent for item in gbp) + proposed_risk
        if gbp_risk > cap + 1e-9:
            return GateDecision(False, "GBP_CORRELATION_CAP", "GBP portfolio risk cap would be exceeded.", expected, actual)

    return GateDecision(True, "APPROVED", "Order matches the final V12 risk profile.", expected, actual)


def make_order_key(symbol: str, engine: str, setup: str, side: str, signal_time: datetime) -> str:
    timestamp = signal_time.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return f"{PROFILE_ID}:{symbol}:{engine}:{setup}:{side.upper()}:{timestamp}"


def validate_profile() -> None:
    """Raise if an accidental edit makes the immutable profile inconsistent."""
    if set(ENGINE_RULES) & set(DISABLED_ENGINES):
        raise RuntimeError("An engine cannot be both allowed and disabled.")
    if {rule.symbol for rule in ENGINE_RULES.values()} - set(ALLOWED_SYMBOLS):
        raise RuntimeError("Engine rule references a symbol outside the profile.")
    if ADAPTIVE_ENGINES != {name for name, rule in ENGINE_RULES.items() if rule.adaptive}:
        raise RuntimeError("Adaptive-engine declaration is inconsistent.")


validate_profile()
