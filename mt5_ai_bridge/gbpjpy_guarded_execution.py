"""GBPJPY-hardened execution wrapper for the V12/V13 MT5 adapter.

All non-GBPJPY orders use the existing executor unchanged. GBPJPY orders gain
one-position-only admission, persistent daily/cooldown stops, and broker-sized
risk capped at 0.20% normally or 0.10% after a loss. Closed GBPJPY positions
are reconciled from MT5 deal history so the guard updates even when SL or TP
closes a position outside the Python process.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from .enums import OrderSide
from .execution import pip_size
from .gbpjpy_guard import GBPJPYGuardConfig, GBPJPYGuardStore
from .v12_final_execution import (
    ENGINE_MAGIC,
    ApprovalSummary,
    ExecutionResult,
    FinalExecutionRequest,
    FinalMT5Executor,
)
from .v12_final_risk import (
    ENGINE_RULES,
    OrderIntent,
    PortfolioSnapshot,
    make_order_key,
    validate_order,
)
from .v12_final_state import StateStore, StoredPosition


class GBPJPYGuardedExecutor(FinalMT5Executor):
    """FinalMT5Executor with fail-closed GBPJPY loss-cluster protection."""

    def __init__(self, client, approval_callback=None,
                 state: Optional[StateStore] = None,
                 max_deviation_points: int = 10,
                 account_mode_provider=None,
                 gbpjpy_guard: Optional[GBPJPYGuardStore] = None,
                 gbpjpy_guard_path: str = "gbpjpy_guard_state.json",
                 gbpjpy_guard_config: GBPJPYGuardConfig = GBPJPYGuardConfig()) -> None:
        super().__init__(
            client=client,
            approval_callback=approval_callback,
            state=state,
            max_deviation_points=max_deviation_points,
            account_mode_provider=account_mode_provider,
        )
        self.gbpjpy_guard = gbpjpy_guard or GBPJPYGuardStore(
            gbpjpy_guard_path, gbpjpy_guard_config
        )

    def reconcile_open_positions(self, account=None,
                                 now: Optional[datetime] = None) -> ExecutionResult:
        """Record broker-closed GBPJPY results before removing stored tickets."""
        now = now or datetime.now(timezone.utc)
        if account is None:
            account, error = self._account()
            if error:
                return error

        positions = self._positions()
        current_tickets = {int(position.ticket) for position in positions}
        missing_gbpjpy = [
            stored for stored in self.state.state.positions.values()
            if stored.symbol.upper() == "GBPJPY"
            and int(stored.ticket) not in current_tickets
        ]
        for stored in missing_gbpjpy:
            r_multiple = self._closed_position_r(
                stored.ticket,
                stored.risk_percent,
                float(account.balance),
            )
            if r_multiple is None:
                return ExecutionResult(
                    False,
                    "GBPJPY_CLOSE_UNRECONCILED",
                    f"Closed GBPJPY ticket {stored.ticket} could not be reconciled "
                    "from MT5 deal history; new GBPJPY entries remain blocked.",
                )
            self.record_closed_trade(
                stored.engine,
                r_multiple,
                now=now,
                symbol=stored.symbol,
                ticket=stored.ticket,
            )

        return super().reconcile_open_positions(account)

    def _closed_position_r(self, ticket: int, risk_percent: float,
                           current_balance: float) -> Optional[float]:
        history = getattr(self.client, "history_deals_get", None)
        if history is None:
            return None
        try:
            deals = list(history(position=int(ticket)) or [])
        except (TypeError, ValueError):
            return None
        if not deals or current_balance <= 0 or risk_percent <= 0:
            return None

        pnl = 0.0
        for deal in deals:
            pnl += float(getattr(deal, "profit", 0.0) or 0.0)
            pnl += float(getattr(deal, "commission", 0.0) or 0.0)
            pnl += float(getattr(deal, "swap", 0.0) or 0.0)
            pnl += float(getattr(deal, "fee", 0.0) or 0.0)
        risk_dollars = current_balance * risk_percent / 100.0
        if risk_dollars <= 0 or not math.isfinite(pnl):
            return None
        return pnl / risk_dollars

    def place(self, request: FinalExecutionRequest,
              now: Optional[datetime] = None) -> ExecutionResult:
        if request.symbol.upper() != "GBPJPY":
            return super().place(request, now=now)

        now = now or datetime.now(timezone.utc)
        account, error = self._account()
        if error:
            return error
        reconciled = self.reconcile_open_positions(account, now=now)
        if not reconciled.ok:
            return reconciled
        self.state.update_equity(float(account.equity), now)

        rule = ENGINE_RULES.get(request.engine)
        if rule is None:
            return ExecutionResult(
                False, "ENGINE_NOT_ALLOWED", f"Unknown engine: {request.engine}."
            )
        if rule.symbol != "GBPJPY":
            return ExecutionResult(
                False, "ENGINE_SYMBOL_MISMATCH",
                "A non-GBPJPY engine cannot submit a GBPJPY order.",
            )
        if request.base_risk_percent not in rule.allowed_risk_percent:
            return ExecutionResult(
                False, "BASE_RISK_MISMATCH",
                "Signal requested a risk tier outside the final profile.",
            )

        open_gbpjpy = sum(
            1 for position in self._positions(symbol="GBPJPY")
            if str(getattr(position, "symbol", "")).upper() == "GBPJPY"
        )
        guard = self.gbpjpy_guard.decision(open_positions=open_gbpjpy, now=now)
        if not guard.ok:
            return ExecutionResult(False, guard.code, guard.message)

        multiplier = self.state.guard_multiplier(request.engine, now)
        profile_risk = request.base_risk_percent * multiplier
        effective_risk = min(profile_risk, guard.risk_cap_percent)
        if effective_risk <= 0:
            return ExecutionResult(
                False, "GBPJPY_GUARD_BLOCKED",
                "GBPJPY risk is currently blocked by the engine or symbol guard.",
            )

        pip = pip_size(self.client, request.symbol)
        tick = self.client.symbol_info_tick(request.symbol)
        info = self.client.symbol_info(request.symbol)
        if pip is None or tick is None or info is None:
            return ExecutionResult(
                False, "MARKET_DATA_UNAVAILABLE",
                "Symbol, tick, or pip data is unavailable.",
            )
        spread_pips = (float(tick.ask) - float(tick.bid)) / pip

        try:
            side = OrderSide(request.side.upper())
        except ValueError:
            return ExecutionResult(False, "SIDE_INVALID", "Side must be BUY or SELL.")
        order_type = (
            self.client.ORDER_TYPE_BUY
            if side is OrderSide.BUY
            else self.client.ORDER_TYPE_SELL
        )
        price = float(tick.ask if side is OrderSide.BUY else tick.bid)
        pip_value = self._pip_value_per_lot(
            request.symbol, order_type, price, pip
        )
        if pip_value <= 0:
            return ExecutionResult(
                False, "PIP_VALUE_UNAVAILABLE",
                "Broker-native pip value could not be calculated.",
            )
        volume = self._risk_volume(
            float(account.balance), effective_risk,
            request.stop_pips, pip_value, info,
        )
        if volume <= 0:
            return ExecutionResult(
                False, "VOLUME_TOO_SMALL",
                "Calculated guarded volume is below the broker minimum.",
            )

        order_key = make_order_key(
            request.symbol, request.engine, request.setup,
            request.side, request.signal_time,
        )
        # Reserve the tested profile risk in the V12 portfolio gate. The actual
        # broker-sized risk is lower and is checked separately by the same gate.
        intent = OrderIntent(
            symbol=request.symbol,
            engine=request.engine,
            setup=request.setup,
            side=request.side,
            requested_risk_percent=profile_risk,
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
            return ExecutionResult(
                False, gate.code, gate.message,
                volume=volume, risk_percent=gate.actual_risk_percent,
            )

        digits = int(getattr(info, "digits", 5) or 5)
        sl = (
            price - request.stop_pips * pip
            if side is OrderSide.BUY
            else price + request.stop_pips * pip
        )
        tp = (
            price + request.target_pips * pip
            if side is OrderSide.BUY
            else price - request.target_pips * pip
        )
        magic = ENGINE_MAGIC[request.engine]
        proposal = ApprovalSummary(
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

        self.state.register_order_key(order_key, now)
        try:
            result = self.client.order_send(broker_request)
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(
                False, "ORDER_SEND_EXCEPTION", str(exc),
                volume=volume, risk_percent=gate.actual_risk_percent,
                proposal=proposal,
            )
        if result is None:
            return ExecutionResult(
                False, "ORDER_SEND_NONE",
                f"order_send returned None: {self.client.last_error()}",
                volume=volume, risk_percent=gate.actual_risk_percent,
                proposal=proposal,
            )
        if not self._successful_retcode(getattr(result, "retcode", None)):
            return ExecutionResult(
                False, "ORDER_REJECTED",
                f"MT5 rejected order: {getattr(result, 'retcode', None)} - "
                f"{getattr(result, 'comment', '')}",
                volume=volume, risk_percent=gate.actual_risk_percent,
                proposal=proposal,
            )

        ticket = self._recover_ticket(request.symbol, magic) or self._result_ticket(result)
        if ticket is None:
            return ExecutionResult(
                False, "TICKET_UNAVAILABLE",
                "MT5 accepted the order but no ticket could be persisted.",
                volume=volume, risk_percent=gate.actual_risk_percent,
                proposal=proposal,
            )
        self.state.register_position(StoredPosition(
            ticket=ticket,
            symbol=request.symbol,
            engine=request.engine,
            side=request.side.upper(),
            risk_percent=gate.actual_risk_percent,
        ))
        self.state.mark_order_opened(request.engine, multiplier)
        return ExecutionResult(
            True,
            "ORDER_FILLED",
            f"GBPJPY order submitted with {guard.code.lower()} protection.",
            ticket=ticket,
            volume=volume,
            risk_percent=gate.actual_risk_percent,
            proposal=proposal,
        )

    def record_closed_trade(self, engine: str, r_multiple: float,
                            now: Optional[datetime] = None,
                            symbol: Optional[str] = None,
                            ticket: Optional[int] = None) -> None:
        super().record_closed_trade(engine, r_multiple, now)
        resolved_symbol = symbol or getattr(ENGINE_RULES.get(engine), "symbol", None)
        if str(resolved_symbol or "").upper() != "GBPJPY":
            return
        self.gbpjpy_guard.record_result(r_multiple, now)
        if ticket is not None:
            self.state.remove_position(ticket)
            return
        matching = [
            stored.ticket for stored in self.state.state.positions.values()
            if stored.symbol.upper() == "GBPJPY" and stored.engine == engine
        ]
        if len(matching) == 1:
            self.state.remove_position(matching[0])
