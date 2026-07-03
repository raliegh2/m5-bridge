"""Named-engine adapter for the final V12 supervised research profile.

Signal generators submit a fully specified ``NamedEngineSignal``. The adapter
converts it into a broker-aware proposal and displays account, symbol, engine,
setup, direction, volume, stop, target, risk, and spread for human review.

This module does not create signals and does not submit broker orders. It is the
mandatory research boundary between the named V12 strategy engines and MT5 data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .v12_final_execution import (
    ApprovalSummary,
    ExecutionResult,
    FinalExecutionRequest,
    FinalResearchExecutor,
)
from .v12_final_state import StateStore


@dataclass(frozen=True)
class NamedEngineSignal:
    symbol: str
    engine: str
    setup: str
    side: str
    base_risk_percent: float
    stop_pips: float
    target_pips: float
    signal_time: datetime

    def __post_init__(self) -> None:
        if self.signal_time.tzinfo is None:
            raise ValueError("signal_time must be timezone-aware")
        if self.stop_pips <= 0 or self.target_pips <= 0:
            raise ValueError("stop_pips and target_pips must be positive")


def console_approval(summary: ApprovalSummary) -> bool:
    """Require an exact confirmation before returning an approved proposal."""
    print("\nFINAL V12 SUPERVISED PROPOSAL REVIEW")
    print(f"Account : {summary.account_login} @ {summary.account_server}")
    print(f"Signal  : {summary.symbol} {summary.side}")
    print(f"Engine  : {summary.engine}")
    print(f"Setup   : {summary.setup}")
    print(f"Volume  : {summary.volume}")
    print(f"Stop/TP : {summary.stop_pips:g} / {summary.target_pips:g} pips")
    print(f"Risk    : {summary.risk_percent:.4f}%")
    print(f"Spread  : {summary.spread_pips:.2f} pips")
    phrase = f"REVIEWED {summary.symbol} {summary.side}"
    answer = input(f"Type exactly '{phrase}' to approve this research proposal: ").strip()
    return answer == phrase


class FinalV12Adapter:
    """Supervised proposal adapter used by named V12 signal engines."""

    def __init__(self, client, state_path: str = "v12_final_research_state.json",
                 approval_callback: Callable[[ApprovalSummary], bool] = console_approval,
                 max_deviation_points: int = 10) -> None:
        self.executor = FinalResearchExecutor(
            client=client,
            approval_callback=approval_callback,
            state=StateStore(state_path),
            max_deviation_points=max_deviation_points,
        )

    def submit(self, signal: NamedEngineSignal,
               now: Optional[datetime] = None) -> ExecutionResult:
        request = FinalExecutionRequest(
            symbol=signal.symbol,
            engine=signal.engine,
            setup=signal.setup,
            side=signal.side,
            base_risk_percent=signal.base_risk_percent,
            stop_pips=signal.stop_pips,
            target_pips=signal.target_pips,
            signal_time=signal.signal_time.astimezone(timezone.utc),
        )
        return self.executor.place(request, now=now)

    def record_closed_trade(self, engine: str, r_multiple: float,
                            now: Optional[datetime] = None) -> None:
        self.executor.record_closed_trade(engine, r_multiple, now)
