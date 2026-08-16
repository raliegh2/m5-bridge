"""A trading system where capital allocation follows evidence mechanically.

The failure this repository documents is not a bad strategy. It is a process
in which a strategy could be deployed on the strength of an in-sample backtest,
because nothing in the code required anything more. `research/SEARCH_RESULT.md`
measured the consequence directly: of the specifications profitable in-sample,
36% (GBPUSD) and 5% (EURUSD) stayed profitable out of sample -- worse than a
coin flip.

So the gate is structural here, not procedural. A :class:`Signal` cannot be
allocated capital until it carries a :class:`Validation` showing out-of-sample
evidence that survives deflation for the number of specifications tried. There
is no flag to skip it. An unvalidated signal is not sized small -- its Kelly
fraction is zero and :class:`~mt5_ai_bridge.risk_v18.RiskEngine` returns no
position.

This has a property worth stating plainly: **the system is correct whether or
not an edge exists.** Today, with the signals measured in this repo, it
allocates nothing and explains why. If your real fill costs are lower than the
presets assume, or a new signal clears the gates, it allocates without anyone
editing a threshold.

Composition
-----------
    Signal  ->  Validation  ->  EdgeGate  ->  RiskEngine  ->  Order plan

Each stage can only reduce exposure. None can increase it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Protocol, Sequence

from .enums import Signal as Side
from .risk_v18 import RiskBudget, RiskDecision, RiskEngine, edge_from_trades

__all__ = [
    "Validation",
    "SignalSpec",
    "EdgeGate",
    "GateResult",
    "TradeIntent",
    "OrderPlan",
    "TradingSystem",
    "SystemStatus",
]


# --- evidence --------------------------------------------------------------


@dataclass(frozen=True)
class Validation:
    """Out-of-sample evidence for one signal specification.

    ``n_trials`` is the number of specifications tried against this data in
    total, not the number tried in the run that produced these figures. It is
    the honest input to deflation and the easiest thing to understate.
    """

    out_of_sample_trades: int
    out_of_sample_profit: float
    profit_factor: float
    positive_fold_fraction: float
    deflated_sharpe: float
    n_trials: int
    trade_profits: Sequence[float] = field(default_factory=tuple)
    note: str = ""

    def edge(self) -> dict:
        return edge_from_trades(list(self.trade_profits))


@dataclass(frozen=True)
class SignalSpec:
    """A named, locked specification plus whatever evidence supports it."""

    name: str
    symbols: tuple
    timeframe: str
    validation: Optional[Validation] = None
    locked_parameters: dict = field(default_factory=dict)


# --- the gate --------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failures: tuple
    checks: dict = field(default_factory=dict)

    def explain(self) -> str:
        if self.passed:
            return "PASS: validated for capital allocation."
        lines = ["BLOCKED: " + ", ".join(self.failures)]
        for name, ok in self.checks.items():
            lines.append(f"  [{'ok' if ok else 'FAIL'}] {name}")
        return "\n".join(lines)


@dataclass(frozen=True)
class EdgeGate:
    """Requirements a signal must meet before it can be traded with capital.

    Defaults match the gates used throughout ``research/``, so a signal that
    passes here is one that passed the same bar the investigation applied.
    """

    min_out_of_sample_trades: int = 200
    min_profit_factor: float = 1.10
    min_positive_fold_fraction: float = 0.5
    min_deflated_sharpe: float = 0.95
    require_positive_profit: bool = True

    def evaluate(self, spec: SignalSpec) -> GateResult:
        v = spec.validation
        if v is None:
            return GateResult(False, ("no validation supplied",),
                              {"validation present": False})

        checks = {
            "positive out-of-sample profit":
                (v.out_of_sample_profit > 0) if self.require_positive_profit
                else True,
            f"profit factor >= {self.min_profit_factor}":
                v.profit_factor >= self.min_profit_factor,
            f"at least {self.min_out_of_sample_trades} OOS trades":
                v.out_of_sample_trades >= self.min_out_of_sample_trades,
            f"more than {self.min_positive_fold_fraction:.0%} of folds positive":
                v.positive_fold_fraction > self.min_positive_fold_fraction,
            f"deflated Sharpe >= {self.min_deflated_sharpe} "
            f"at {v.n_trials} trials":
                v.deflated_sharpe >= self.min_deflated_sharpe,
        }
        failures = tuple(name for name, ok in checks.items() if not ok)
        return GateResult(not failures, failures, checks)


# --- intents and plans -----------------------------------------------------


@dataclass(frozen=True)
class TradeIntent:
    """What a signal wants to do, before risk has had its say."""

    symbol: str
    side: Side
    entry: float
    stop: float
    pip: float
    pip_value_per_lot: float
    reason: str = ""

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.stop)

    def validate(self) -> None:
        if self.stop_distance <= 0:
            raise ValueError(f"{self.symbol}: stop must differ from entry")
        if self.side is Side.BUY and self.stop >= self.entry:
            raise ValueError(f"{self.symbol}: long stop must sit below entry")
        if self.side is Side.SELL and self.stop <= self.entry:
            raise ValueError(f"{self.symbol}: short stop must sit above entry")


@dataclass(frozen=True)
class OrderPlan:
    """A sized, approved order -- or a refusal with its reason."""

    intent: TradeIntent
    approved: bool
    lots: float
    risk_fraction: float
    reason: str
    detail: dict = field(default_factory=dict)

    def describe(self) -> str:
        if not self.approved:
            return f"{self.intent.symbol}: NO TRADE -- {self.reason}"
        return (f"{self.intent.symbol}: {self.intent.side.value} "
                f"{self.lots:.2f} lots, stop {self.intent.stop:.5f}, "
                f"risking {self.risk_fraction:.2%} -- {self.reason}")


@dataclass
class SystemStatus:
    equity: float
    peak_equity: float
    drawdown: float
    open_risk: Dict[str, float]
    tradeable_signals: List[str]
    blocked_signals: Dict[str, str]

    def describe(self) -> str:
        lines = [
            f"Equity {self.equity:,.2f}  peak {self.peak_equity:,.2f}  "
            f"drawdown {self.drawdown:.2%}",
            f"Open risk: {sum(self.open_risk.values()):.2%} across "
            f"{len(self.open_risk)} position(s)",
        ]
        if self.tradeable_signals:
            lines.append(f"Allocating to: {', '.join(self.tradeable_signals)}")
        else:
            lines.append("Allocating to: nothing -- no signal has cleared the "
                         "edge gate")
        for name, why in self.blocked_signals.items():
            lines.append(f"  blocked: {name} -- {why}")
        return "\n".join(lines)


# --- the system ------------------------------------------------------------


class TradingSystem:
    """Signals in, sized orders out, with evidence required at every step."""

    def __init__(self, *, gate: Optional[EdgeGate] = None,
                 risk: Optional[RiskEngine] = None,
                 starting_equity: float = 10_000.0) -> None:
        self.gate = gate or EdgeGate()
        self.risk = risk or RiskEngine()
        self.equity = float(starting_equity)
        self.balance = float(starting_equity)
        self._signals: Dict[str, SignalSpec] = {}
        self._gate_results: Dict[str, GateResult] = {}
        self._open_risk: Dict[str, float] = {}
        self.risk.governor.observe(self.equity)

    # -- registration --
    def register(self, spec: SignalSpec) -> GateResult:
        """Add a signal and evaluate it against the gate immediately."""
        result = self.gate.evaluate(spec)
        self._signals[spec.name] = spec
        self._gate_results[spec.name] = result
        return result

    @property
    def tradeable(self) -> List[str]:
        return [n for n, r in self._gate_results.items() if r.passed]

    @property
    def blocked(self) -> Dict[str, str]:
        return {n: ", ".join(r.failures)
                for n, r in self._gate_results.items() if not r.passed}

    # -- account state --
    def mark(self, equity: float, balance: Optional[float] = None,
             now: Optional[datetime] = None) -> None:
        """Update account state; call before planning."""
        self.equity = float(equity)
        if balance is not None:
            self.balance = float(balance)
        self.risk.governor.observe(self.equity)
        stamp = (now or datetime.now(timezone.utc)).date().isoformat()
        self.risk.kill_switch.start_day(stamp, self.equity)

    def record_close(self, symbol: str, profit: float) -> None:
        """Book a closed trade so the kill switch and equity stay current."""
        self.balance += float(profit)
        self.equity = self.balance
        self.risk.kill_switch.record_trade(float(profit))
        self._open_risk.pop(symbol, None)
        self.risk.governor.observe(self.equity)

    # -- planning --
    def plan(self, signal_name: str, intent: TradeIntent) -> OrderPlan:
        """Turn one intent into a sized order, or refuse it with a reason."""
        intent.validate()

        spec = self._signals.get(signal_name)
        if spec is None:
            return OrderPlan(intent, False, 0.0, 0.0,
                             f"unknown signal {signal_name!r}: register it "
                             "with its validation first")

        result = self._gate_results[signal_name]
        if not result.passed:
            return OrderPlan(
                intent, False, 0.0, 0.0,
                f"signal {signal_name!r} has not cleared the edge gate "
                f"({', '.join(result.failures)})",
                {"gate": result.checks})

        if intent.symbol not in spec.symbols:
            return OrderPlan(intent, False, 0.0, 0.0,
                             f"{intent.symbol} is not in {signal_name!r}'s "
                             f"validated symbol set {spec.symbols}")

        decision: RiskDecision = self.risk.size(
            symbol=intent.symbol, balance=self.balance, equity=self.equity,
            stop_distance=intent.stop_distance, pip=intent.pip,
            pip_value_per_lot=intent.pip_value_per_lot,
            edge=spec.validation.edge(), open_risk=self._open_risk)

        if decision.approved:
            self._open_risk[intent.symbol] = decision.risk_fraction

        return OrderPlan(intent, decision.approved, decision.lots,
                         decision.risk_fraction, decision.reason,
                         decision.detail)

    def plan_all(self, intents: Dict[str, Sequence[TradeIntent]]
                 ) -> List[OrderPlan]:
        """Plan many intents, keyed by signal name, in a single pass."""
        plans = []
        for signal_name, items in intents.items():
            for intent in items:
                plans.append(self.plan(signal_name, intent))
        return plans

    # -- reporting --
    def status(self) -> SystemStatus:
        return SystemStatus(
            equity=self.equity,
            peak_equity=self.risk.governor.peak_equity,
            drawdown=self.risk.governor.drawdown(self.equity),
            open_risk=dict(self._open_risk),
            tradeable_signals=self.tradeable,
            blocked_signals=self.blocked,
        )

    def report(self) -> str:
        lines = [self.status().describe(), ""]
        for name, result in self._gate_results.items():
            lines.append(f"Signal {name!r}:")
            lines.append("  " + result.explain().replace("\n", "\n  "))
        return "\n".join(lines)
