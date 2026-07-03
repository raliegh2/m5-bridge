"""Supervised order executor for the final V12 strategy profile.

Every named V12 signal must route through ``FinalResearchExecutor``. Account type
is not used as a gate, but unattended execution is prohibited: an explicit
approval callback must return True for every order after the full risk check.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .enums import OrderSide
from .execution import pip_size
from .v12_final_risk import ENGINE_RULES, OrderIntent, PortfolioSnapshot, make_order_key, validate_order
from .v12_final_state import StateStore, StoredPosition


@dataclass(frozen=True)
class FinalExecutionRequest:
    symbol: str
    engine: str
    setup: str
    side: str
    base_risk_percent: float
    stop_pips: float
    target_pips: float
    signal_time: datetime


@dataclass(frozen=True)
class ApprovalSummary:
    symbol: str
    engine: str
    setup: str
    side: str
    volume: float
    stop_pips: float
    target_pips: float
    risk_percent: float
    spread_pips: float
    account_login: Optional[int]
    account_server: str


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    code: str
    message: str
    ticket: Optional[int] = None
    volume: float = 0.0
    risk_percent: float = 0.0


ApprovalCallback = Callable[[ApprovalSummary], bool]


class FinalResearchExecutor:
    def __init__(self, client, approval_callback: ApprovalCallback,
                 state: Optional[StateStore] = None,
                 max_deviation_points: int = 10) -> None:
        if approval_callback is None:
            raise ValueError("An explicit approval callback is required.")
        self.client = client
        self.approval_callback = approval_callback
        self.state = state or StateStore()
        self.max_deviation_points = int(max_deviation_points)

    def place(self, request: FinalExecutionRequest,
              now: Optional[datetime] = None) -> ExecutionResult:
        now = now or datetime.now(timezone.utc)
        account = self.client.account_info()
        if account is None:
            return ExecutionResult(False, "ACCOUNT_UNAVAILABLE", "MT5 account information is unavailable.")
        positions = list(self.client.positions_get() or [])
        current_tickets = {int(position.ticket) for position in positions}
        registered_tickets = {value.ticket for value in self.state.state.positions.values()}
        unknown = current_tickets - registered_tickets
        if unknown:
            return ExecutionResult(False, "UNREGISTERED_POSITION",
                                   "Manual or unregistered positions are open; portfolio risk cannot be calculated.")
        self.state.sync_open_tickets(current_tickets)
        self.state.update_equity(float(account.equity), now)

        rule = ENGINE_RULES.get(request.engine)
        if rule is None:
            return ExecutionResult(False, "ENGINE_NOT_ALLOWED", f"Unknown engine: {request.engine}.")
        if request.base_risk_percent not in rule.allowed_risk_percent:
            return ExecutionResult(False, "BASE_RISK_MISMATCH", "Signal requested a risk tier outside the final profile.")

        multiplier = self.state.guard_multiplier(request.engine, now)
        requested_risk = request.base_risk_percent * multiplier
        pip = pip_size(self.client, request.symbol)
        tick = self.client.symbol_info_tick(request.symbol)
        info = self.client.symbol_info(request.symbol)
        if pip is None or tick is None or info is None:
            return ExecutionResult(False, "MARKET_DATA_UNAVAILABLE", "Symbol, tick, or pip data is unavailable.")
        spread_pips = (float(tick.ask) - float(tick.bid)) / pip

        try:
            side = OrderSide(request.side.upper())
        except ValueError:
            return ExecutionResult(False, "SIDE_INVALID", "Side must be BUY or SELL.")
        order_type = self.client.ORDER_TYPE_BUY if side is OrderSide.BUY else self.client.ORDER_TYPE_SELL
        price = float(tick.ask if side is OrderSide.BUY else tick.bid)
        pip_value = self._pip_value_per_lot(request.symbol, order_type, price, pip)
        if pip_value <= 0:
            return ExecutionResult(False, "PIP_VALUE_UNAVAILABLE", "Broker-native pip value could not be calculated.")

        volume = self._risk_volume(float(account.balance), requested_risk,
                                   request.stop_pips, pip_value, info)
        if volume <= 0:
            return ExecutionResult(False, "VOLUME_TOO_SMALL", "Calculated volume is below the broker minimum.")

        order_key = make_order_key(request.symbol, request.engine, request.setup,
                                   request.side, request.signal_time)
        intent = OrderIntent(
            symbol=request.symbol,
            engine=request.engine,
            setup=request.setup,
            side=request.side,
            requested_risk_percent=requested_risk,
            guard_multiplier=multiplier,
            stop_pips=request.stop_pips,
            volume=volume,
            pip_value_per_lot=pip_value,
            spread_pips=spread_pips,
            order_key=order_key,
        )
        snapshot = PortfolioSnapshot(
            balance=float(account.balance),
            equity=float(account.equity),
            day_start_equity=self.state.state.day_start_equity,
            peak_equity=self.state.state.peak_equity,
            open_risk=self.state.open_risk(),
            recent_order_keys=frozenset(self.state.state.recent_orders),
            now=now,
        )
        gate = validate_order(intent, snapshot)
        if not gate.ok:
            return ExecutionResult(False, gate.code, gate.message, volume=volume,
                                   risk_percent=gate.actual_risk_percent)

        approval = ApprovalSummary(
            symbol=request.symbol,
            engine=request.engine,
            setup=request.setup,
            side=request.side.upper(),
            volume=volume,
            stop_pips=request.stop_pips,
            target_pips=request.target_pips,
            risk_percent=gate.actual_risk_percent,
            spread_pips=spread_pips,
            account_login=getattr(account, "login", None),
            account_server=str(getattr(account, "server", "")),
        )
        if not self.approval_callback(approval):
            return ExecutionResult(False, "USER_DECLINED", "Order was declined during supervised review.",
                                   volume=volume, risk_percent=gate.actual_risk_percent)

        stop = price - request.stop_pips * pip if side is OrderSide.BUY else price + request.stop_pips * pip
        target = price + request.target_pips * pip if side is OrderSide.BUY else price - request.target_pips * pip
        digits = int(getattr(info, "digits", 5))
        broker_request = {
            "action": self.client.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": round(stop, digits),
            "tp": round(target, digits),
            "deviation": self.max_deviation_points,
            "magic": self._magic(request.engine),
            "comment": self._comment(request.engine, request.setup),
            "type_time": self.client.ORDER_TIME_GTC,
        }
        result = self.client.order_send(broker_request)
        if result is None:
            return ExecutionResult(False, "ORDER_SEND_FAILED", f"Order failed: {self.client.last_error()}.",
                                   volume=volume, risk_percent=gate.actual_risk_percent)
        if result.retcode != self.client.TRADE_RETCODE_DONE:
            return ExecutionResult(False, "ORDER_REJECTED",
                                   f"Order rejected: {result.retcode} - {result.comment}",
                                   volume=volume, risk_percent=gate.actual_risk_percent)

        ticket = self._ticket(result)
        if ticket is None:
            return ExecutionResult(False, "TICKET_MISSING",
                                   "Broker accepted the order but returned no trackable ticket.",
                                   volume=volume, risk_percent=gate.actual_risk_percent)
        self.state.register_order_key(order_key, now)
        self.state.register_position(StoredPosition(
            ticket=ticket,
            symbol=request.symbol,
            engine=request.engine,
            side=request.side.upper(),
            risk_percent=requested_risk,
        ))
        self.state.mark_order_opened(request.engine, multiplier)
        return ExecutionResult(True, "FILLED", "Supervised final-profile order placed.", ticket,
                               volume, gate.actual_risk_percent)

    def record_closed_trade(self, engine: str, r_multiple: float,
                            now: Optional[datetime] = None) -> None:
        self.state.record_trade_result(engine, r_multiple, now)

    def _pip_value_per_lot(self, symbol: str, order_type: int,
                           price: float, pip: float) -> float:
        calculator = getattr(self.client, "order_calc_profit", None)
        if calculator is None:
            return 0.0
        close_price = price + pip if order_type == self.client.ORDER_TYPE_BUY else price - pip
        result = calculator(order_type, symbol, 1.0, price, close_price)
        return abs(float(result)) if result is not None and math.isfinite(float(result)) else 0.0

    @staticmethod
    def _risk_volume(balance: float, risk_percent: float, stop_pips: float,
                     pip_value_per_lot: float, symbol_info) -> float:
        if balance <= 0 or risk_percent <= 0 or stop_pips <= 0 or pip_value_per_lot <= 0:
            return 0.0
        raw = balance * risk_percent / 100.0 / (stop_pips * pip_value_per_lot)
        step = float(getattr(symbol_info, "volume_step", 0.01) or 0.01)
        minimum = float(getattr(symbol_info, "volume_min", step) or step)
        maximum = float(getattr(symbol_info, "volume_max", raw) or raw)
        floored = math.floor((raw + 1e-12) / step) * step
        if floored < minimum:
            return 0.0
        return round(min(floored, maximum), 8)

    @staticmethod
    def _ticket(result) -> Optional[int]:
        for name in ("position", "order", "deal"):
            value = getattr(result, name, None)
            if value:
                return int(value)
        return None

    @staticmethod
    def _magic(engine: str) -> int:
        ordered = sorted(ENGINE_RULES)
        return 20261200 + ordered.index(engine) + 1

    @staticmethod
    def _comment(engine: str, setup: str) -> str:
        return f"V12:{engine}:{setup}"[:31]


# Backward-compatible import name; behavior is now supervised research only.
FinalDemoExecutor = FinalResearchExecutor
