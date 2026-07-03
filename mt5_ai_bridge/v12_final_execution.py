"""Automatic, demo-only MetaTrader 5 execution for the frozen V12 profile."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .enums import OrderSide
from .execution import pip_size
from .v12_final_risk import (
    ENGINE_RULES,
    OrderIntent,
    PortfolioSnapshot,
    make_order_key,
    validate_order,
)
from .v12_final_state import StateStore, StoredPosition


ENGINE_MAGIC = {
    engine: 20261200 + index
    for index, engine in enumerate(sorted(ENGINE_RULES), start=1)
}
MAGIC_ENGINE = {magic: engine for engine, magic in ENGINE_MAGIC.items()}


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
    proposal: Optional[ApprovalSummary] = None


class FinalMT5Executor:
    def __init__(self, client, approval_callback=None,
                 state: Optional[StateStore] = None,
                 max_deviation_points: int = 10,
                 account_mode_provider: Optional[Callable[[], str]] = None) -> None:
        # Retained for source compatibility with the former supervised
        # executor. Automatic demo execution intentionally ignores callbacks.
        if isinstance(approval_callback, StateStore) and state is None:
            state = approval_callback
        self.client = client
        self.state = state or StateStore()
        self.max_deviation_points = int(max_deviation_points)
        self.account_mode_provider = account_mode_provider or (lambda: "DEMO")

    def _account(self):
        account = self.client.account_info()
        if account is None:
            return None, ExecutionResult(
                False, "ACCOUNT_UNAVAILABLE", "MT5 account information is unavailable.")
        selected = str(self.account_mode_provider()).strip().upper()
        expected = {
            "DEMO": getattr(self.client, "ACCOUNT_TRADE_MODE_DEMO", 0),
            "LIVE": getattr(self.client, "ACCOUNT_TRADE_MODE_REAL", 2),
        }.get(selected)
        if expected is None:
            return None, ExecutionResult(
                False, "ACCOUNT_MODE_INVALID", "Selected account mode must be DEMO or LIVE.")
        if getattr(account, "trade_mode", None) != expected:
            return None, ExecutionResult(
                False, "ACCOUNT_MODE_MISMATCH",
                f"Selected {selected}, but the connected MT5 account has a different trade mode.")
        return account, None

    def _positions(self, **kwargs):
        try:
            return list(self.client.positions_get(**kwargs) or [])
        except TypeError:
            positions = list(self.client.positions_get() or [])
            ticket = kwargs.get("ticket")
            symbol = kwargs.get("symbol")
            if ticket is not None:
                positions = [p for p in positions if int(p.ticket) == int(ticket)]
            if symbol is not None:
                positions = [p for p in positions if p.symbol == symbol]
            return positions

    def reconcile_open_positions(self, account=None) -> ExecutionResult:
        """Recover V12 positions after restart and remove locally closed tickets."""
        if account is None:
            account, error = self._account()
            if error:
                return error
        positions = self._positions()
        current_tickets = {int(position.ticket) for position in positions}
        self.state.sync_open_tickets(current_tickets)
        unknown = []
        for position in positions:
            ticket = int(position.ticket)
            stored = self.state.state.positions.get(str(ticket))
            engine = (stored.engine if stored else
                      MAGIC_ENGINE.get(int(getattr(position, "magic", 0) or 0)))
            risk = self._position_risk_percent(position, float(account.balance))
            if engine is None or risk is None:
                unknown.append(ticket)
                continue
            side = ("BUY" if int(position.type) == self.client.POSITION_TYPE_BUY
                    else "SELL")
            self.state.register_position(StoredPosition(
                ticket=ticket, symbol=str(position.symbol), engine=engine,
                side=side, risk_percent=risk,
            ))
        if unknown:
            return ExecutionResult(
                False, "UNREGISTERED_POSITION",
                f"Could not reconcile open ticket(s): {sorted(unknown)}.")
        return ExecutionResult(True, "RECONCILED", "Open V12 positions reconciled.")

    def place(self, request: FinalExecutionRequest,
              now: Optional[datetime] = None) -> ExecutionResult:
        now = now or datetime.now(timezone.utc)
        account, error = self._account()
        if error:
            return error
        reconciled = self.reconcile_open_positions(account)
        if not reconciled.ok:
            return reconciled
        self.state.update_equity(float(account.equity), now)

        rule = ENGINE_RULES.get(request.engine)
        if rule is None:
            return ExecutionResult(False, "ENGINE_NOT_ALLOWED", f"Unknown engine: {request.engine}.")
        if request.base_risk_percent not in rule.allowed_risk_percent:
            return ExecutionResult(False, "BASE_RISK_MISMATCH",
                                   "Signal requested a risk tier outside the final profile.")

        multiplier = self.state.guard_multiplier(request.engine, now)
        requested_risk = request.base_risk_percent * multiplier
        pip = pip_size(self.client, request.symbol)
        tick = self.client.symbol_info_tick(request.symbol)
        info = self.client.symbol_info(request.symbol)
        if pip is None or tick is None or info is None:
            return ExecutionResult(False, "MARKET_DATA_UNAVAILABLE",
                                   "Symbol, tick, or pip data is unavailable.")
        spread_pips = (float(tick.ask) - float(tick.bid)) / pip

        try:
            side = OrderSide(request.side.upper())
        except ValueError:
            return ExecutionResult(False, "SIDE_INVALID", "Side must be BUY or SELL.")
        order_type = (self.client.ORDER_TYPE_BUY if side is OrderSide.BUY
                      else self.client.ORDER_TYPE_SELL)
        price = float(tick.ask if side is OrderSide.BUY else tick.bid)
        pip_value = self._pip_value_per_lot(request.symbol, order_type, price, pip)
        if pip_value <= 0:
            return ExecutionResult(False, "PIP_VALUE_UNAVAILABLE",
                                   "Broker-native pip value could not be calculated.")
        volume = self._risk_volume(float(account.balance), requested_risk,
                                   request.stop_pips, pip_value, info)
        if volume <= 0:
            return ExecutionResult(False, "VOLUME_TOO_SMALL",
                                   "Calculated volume is below the broker minimum.")

        order_key = make_order_key(request.symbol, request.engine, request.setup,
                                   request.side, request.signal_time)
        intent = OrderIntent(
            symbol=request.symbol, engine=request.engine, setup=request.setup,
            side=request.side, requested_risk_percent=requested_risk,
            guard_multiplier=multiplier, stop_pips=request.stop_pips,
            volume=volume, pip_value_per_lot=pip_value,
            spread_pips=spread_pips, order_key=order_key,
        )
        snapshot = PortfolioSnapshot(
            balance=float(account.balance), equity=float(account.equity),
            day_start_equity=self.state.state.day_start_equity,
            peak_equity=self.state.state.peak_equity,
            open_risk=self.state.open_risk(),
            recent_order_keys=frozenset(self.state.state.recent_orders), now=now,
        )
        gate = validate_order(intent, snapshot)
        if not gate.ok:
            return ExecutionResult(False, gate.code, gate.message, volume=volume,
                                   risk_percent=gate.actual_risk_percent)

        digits = int(getattr(info, "digits", 5) or 5)
        sl = price - request.stop_pips * pip if side is OrderSide.BUY \
            else price + request.stop_pips * pip
        tp = price + request.target_pips * pip if side is OrderSide.BUY \
            else price - request.target_pips * pip
        magic = ENGINE_MAGIC[request.engine]
        proposal = ApprovalSummary(
            symbol=request.symbol, engine=request.engine, setup=request.setup,
            side=request.side.upper(), volume=volume,
            stop_pips=request.stop_pips, target_pips=request.target_pips,
            risk_percent=gate.actual_risk_percent, spread_pips=spread_pips,
            account_login=getattr(account, "login", None),
            account_server=str(getattr(account, "server", "")),
        )
        broker_request = {
            "action": self.client.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": volume,
            "type": order_type,
            "price": round(price, digits),
            "sl": round(sl, digits),
            "tp": round(tp, digits),
            "deviation": self.max_deviation_points,
            "magic": magic,
            "comment": self._comment(request.engine),
            "type_time": self.client.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }

        # Persist the duplicate key before transmission. If the process dies
        # after order_send, restart recovery will not submit the signal twice.
        self.state.register_order_key(order_key, now)
        try:
            result = self.client.order_send(broker_request)
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(False, "ORDER_SEND_EXCEPTION", str(exc),
                                   volume=volume, risk_percent=gate.actual_risk_percent,
                                   proposal=proposal)
        if result is None:
            return ExecutionResult(False, "ORDER_SEND_NONE",
                                   f"order_send returned None: {self.client.last_error()}",
                                   volume=volume, risk_percent=gate.actual_risk_percent,
                                   proposal=proposal)
        if not self._successful_retcode(getattr(result, "retcode", None)):
            return ExecutionResult(
                False, "ORDER_REJECTED",
                f"MT5 rejected order: {getattr(result, 'retcode', None)} - "
                f"{getattr(result, 'comment', '')}", volume=volume,
                risk_percent=gate.actual_risk_percent, proposal=proposal)

        ticket = self._recover_ticket(request.symbol, magic) or self._result_ticket(result)
        if ticket is None:
            return ExecutionResult(
                False, "TICKET_UNAVAILABLE",
                "MT5 accepted the order but no ticket could be persisted.",
                volume=volume, risk_percent=gate.actual_risk_percent,
                proposal=proposal)
        self.state.register_position(StoredPosition(
            ticket=ticket, symbol=request.symbol, engine=request.engine,
            side=request.side.upper(), risk_percent=gate.actual_risk_percent,
        ))
        self.state.mark_order_opened(request.engine, multiplier)
        return ExecutionResult(
            True, "ORDER_FILLED", "V12 order submitted and persisted.",
            ticket=ticket, volume=volume,
            risk_percent=gate.actual_risk_percent, proposal=proposal)

    def modify(self, ticket: int, stop_loss: Optional[float] = None,
               take_profit: Optional[float] = None) -> ExecutionResult:
        account, error = self._account()
        if error:
            return error
        positions = self._positions(ticket=ticket)
        if not positions:
            return ExecutionResult(False, "POSITION_NOT_FOUND", f"Ticket {ticket} is not open.")
        position = positions[0]
        info = self.client.symbol_info(position.symbol)
        digits = int(getattr(info, "digits", 5) or 5)
        request = {
            "action": self.client.TRADE_ACTION_SLTP,
            "position": int(ticket), "symbol": position.symbol,
            "sl": round(float(stop_loss if stop_loss is not None else position.sl), digits),
            "tp": round(float(take_profit if take_profit is not None else position.tp), digits),
            "magic": int(getattr(position, "magic", 0) or 0),
            "comment": "V12 modify",
        }
        result = self._management_send(request, "POSITION_MODIFIED", int(ticket))
        if result.ok:
            self.reconcile_open_positions(account)
        return result

    def close(self, ticket: int, volume: Optional[float] = None) -> ExecutionResult:
        account, error = self._account()
        if error:
            return error
        positions = self._positions(ticket=ticket)
        if not positions:
            return ExecutionResult(False, "POSITION_NOT_FOUND", f"Ticket {ticket} is not open.")
        position = positions[0]
        tick = self.client.symbol_info_tick(position.symbol)
        info = self.client.symbol_info(position.symbol)
        is_buy = int(position.type) == self.client.POSITION_TYPE_BUY
        close_type = self.client.ORDER_TYPE_SELL if is_buy else self.client.ORDER_TYPE_BUY
        price = float(tick.bid if is_buy else tick.ask)
        digits = int(getattr(info, "digits", 5) or 5)
        request = {
            "action": self.client.TRADE_ACTION_DEAL,
            "position": int(ticket), "symbol": position.symbol,
            "volume": float(volume if volume is not None else position.volume),
            "type": close_type, "price": round(price, digits),
            "deviation": self.max_deviation_points,
            "magic": int(getattr(position, "magic", 0) or 0),
            "comment": "V12 close", "type_time": self.client.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }
        result = self._management_send(request, "POSITION_CLOSED", int(ticket))
        if result.ok:
            if float(request["volume"]) + 1e-12 >= float(position.volume):
                self.state.remove_position(ticket)
            else:
                self.reconcile_open_positions(account)
        return result

    def record_closed_trade(self, engine: str, r_multiple: float,
                            now: Optional[datetime] = None) -> None:
        self.state.record_trade_result(engine, r_multiple, now)

    def _management_send(self, request: dict, success_code: str,
                         ticket: int) -> ExecutionResult:
        result = self.client.order_send(request)
        if result is None:
            return ExecutionResult(False, "ORDER_SEND_NONE",
                                   f"order_send returned None: {self.client.last_error()}")
        if not self._successful_retcode(getattr(result, "retcode", None)):
            return ExecutionResult(False, "ORDER_REJECTED",
                                   f"MT5 rejected request: {getattr(result, 'retcode', None)}")
        return ExecutionResult(True, success_code, success_code.replace("_", " ").title(),
                               ticket=ticket)

    def _position_risk_percent(self, position, balance: float) -> Optional[float]:
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        stop = float(getattr(position, "sl", 0.0) or 0.0)
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        pip = pip_size(self.client, position.symbol)
        if entry <= 0 or stop <= 0 or volume <= 0 or not pip:
            return None
        order_type = (self.client.ORDER_TYPE_BUY
                      if int(position.type) == self.client.POSITION_TYPE_BUY
                      else self.client.ORDER_TYPE_SELL)
        pip_value = self._pip_value_per_lot(position.symbol, order_type, entry, pip)
        if pip_value <= 0 or balance <= 0:
            return None
        stop_pips = abs(entry - stop) / pip
        return stop_pips * volume * pip_value / balance * 100.0

    def _recover_ticket(self, symbol: str, magic: int) -> Optional[int]:
        matches = [p for p in self._positions(symbol=symbol)
                   if int(getattr(p, "magic", 0) or 0) == magic]
        return max((int(p.ticket) for p in matches), default=None)

    def _successful_retcode(self, retcode) -> bool:
        accepted = {getattr(self.client, "TRADE_RETCODE_DONE", None)}
        accepted.add(getattr(self.client, "TRADE_RETCODE_PLACED", None))
        accepted.add(getattr(self.client, "TRADE_RETCODE_DONE_PARTIAL", None))
        return retcode is not None and retcode in {value for value in accepted if value is not None}

    def _filling_mode(self, symbol_info) -> int:
        native = getattr(symbol_info, "filling_mode", None)
        if native is not None:
            return int(native)
        return int(getattr(self.client, "ORDER_FILLING_IOC", 1))

    @staticmethod
    def _comment(engine: str) -> str:
        return ("V12 " + engine)[:31]

    @staticmethod
    def _result_ticket(result) -> Optional[int]:
        for field in ("order", "deal"):
            value = int(getattr(result, field, 0) or 0)
            if value > 0:
                return value
        return None

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


# Compatibility names retained for existing imports on the V12 branch.
FinalResearchExecutor = FinalMT5Executor
FinalDemoExecutor = FinalMT5Executor
