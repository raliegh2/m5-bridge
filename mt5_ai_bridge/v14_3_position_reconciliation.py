"""Reliable MT5 position/deal reconciliation for the V14.3 parity runner.

MT5 order, deal, position-ticket, and position-identifier values are distinct.
The original live adapter persisted ``order_send().order`` as though it were
always the position ticket. On some brokers that prevents a closed position
from being matched to its exit deals, so post-loss controls can remain stale.

This adapter:

* snapshots open position tickets before transmission;
* resolves the actual newly opened MT5 position after an accepted order;
* persists order/deal/position identifiers separately;
* remaps positions by the stable MT5 position identifier;
* falls back to deal-history matching by order/deal ticket, magic, symbol and
  opening time;
* records a closed result exactly once before the next admission decision.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .v14_3_live_execution import (
    ExecutionResult,
    LiveSignal,
    MAGIC_BY_ENGINE,
)
from .v14_3_research_parity_execution import (
    ResearchParityLiveExecutor,
    ResearchParityLiveRunnerConfig,
    ResearchParityState,
)


class ReconciledResearchParityState(ResearchParityState):
    """Research state with idempotent closed-position bookkeeping."""

    def _default(self) -> dict[str, Any]:
        payload = super()._default()
        payload["processed_closed_positions"] = {}
        return payload

    def was_closed_processed(self, identifier: int) -> bool:
        return str(int(identifier)) in self.data.setdefault(
            "processed_closed_positions", {}
        )

    def mark_closed_processed(self, identifier: int, closed_at: datetime) -> None:
        values = self.data.setdefault("processed_closed_positions", {})
        values[str(int(identifier))] = closed_at.astimezone(timezone.utc).isoformat()
        if len(values) > 10000:
            ordered = sorted(values.items(), key=lambda item: item[1])[-8000:]
            self.data["processed_closed_positions"] = dict(ordered)
        self.save()


class ReconciledResearchParityLiveExecutor(ResearchParityLiveExecutor):
    """Research-parity executor with broker-correct position reconciliation."""

    def __init__(
        self,
        client: Any,
        config: ResearchParityLiveRunnerConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> None:
        self.client = client
        self.config = config
        self.state = ReconciledResearchParityState(config.state_path)
        self.approval_callback = approval_callback or self._console_approval
        from .v14_3_research_parity_execution import PARITY_DRAWDOWN_GOVERNOR

        self.governor = PARITY_DRAWDOWN_GOVERNOR

    @staticmethod
    def _position_ticket(position: Any) -> int:
        return int(getattr(position, "ticket", 0) or 0)

    @staticmethod
    def _position_identifier(position: Any) -> int:
        return int(
            getattr(position, "identifier", 0)
            or getattr(position, "ticket", 0)
            or 0
        )

    @staticmethod
    def _position_time_msc(position: Any) -> int:
        value = int(getattr(position, "time_msc", 0) or 0)
        if value:
            return value
        return int(getattr(position, "time", 0) or 0) * 1000

    def _matching_open_position(
        self,
        signal: LiveSignal,
        before_tickets: set[int],
        expected_volume: float,
    ) -> Any | None:
        positions = list(self.client.positions_get(symbol=signal.broker_symbol) or [])
        expected_magic = MAGIC_BY_ENGINE.get(signal.engine, 20264399)
        expected_type = (
            self.client.POSITION_TYPE_BUY
            if signal.side.upper() == "BUY"
            else self.client.POSITION_TYPE_SELL
        )

        def compatible(position: Any) -> bool:
            if int(getattr(position, "magic", 0) or 0) != int(expected_magic):
                return False
            if int(getattr(position, "type", -1)) != int(expected_type):
                return False
            volume = float(getattr(position, "volume", 0.0) or 0.0)
            tolerance = max(0.000001, expected_volume * 0.02)
            return abs(volume - expected_volume) <= tolerance

        candidates = [position for position in positions if compatible(position)]
        newly_opened = [
            position
            for position in candidates
            if self._position_ticket(position) not in before_tickets
        ]
        pool = newly_opened or candidates
        if not pool:
            return None
        return max(
            pool,
            key=lambda position: (
                self._position_time_msc(position),
                self._position_ticket(position),
            ),
        )

    def _remap_registered_position(
        self,
        broker_result_ticket: int | None,
        actual_position: Any,
        result: ExecutionResult,
    ) -> int:
        actual_ticket = self._position_ticket(actual_position)
        identifier = self._position_identifier(actual_position)
        old_key = str(int(broker_result_ticket or 0))
        stored = self.state.data["positions"].pop(old_key, None)
        if stored is None:
            stored = self.state.data["positions"].get(str(actual_ticket))
        if stored is None:
            return int(broker_result_ticket or actual_ticket)

        request = dict((result.proposal or {}).get("request") or {})
        stored.update(
            {
                "ticket": actual_ticket,
                "position_ticket": actual_ticket,
                "position_identifier": identifier,
                "order_ticket": int(getattr(result, "ticket", 0) or 0),
                "deal_ticket": int(
                    (result.proposal or {}).get("broker_deal_ticket", 0) or 0
                ),
                "magic": int(request.get("magic", 0) or 0),
            }
        )
        self.state.data["positions"][str(actual_ticket)] = stored
        self.state.save()
        return actual_ticket

    def place(
        self,
        signal: LiveSignal,
        now: Optional[datetime] = None,
    ) -> ExecutionResult:
        before_tickets = {
            self._position_ticket(position)
            for position in self.client.positions_get(symbol=signal.broker_symbol) or []
        }
        result = super().place(signal, now=now)
        if result.code != "ORDER_FILLED":
            return result

        actual = self._matching_open_position(signal, before_tickets, result.volume)
        if actual is None:
            return result
        actual_ticket = self._remap_registered_position(result.ticket, actual, result)
        return replace(result, ticket=actual_ticket)

    @staticmethod
    def _deal_time(deal: Any) -> datetime:
        raw = int(getattr(deal, "time", 0) or 0)
        return (
            datetime.fromtimestamp(raw, tz=timezone.utc)
            if raw > 0
            else datetime.now(timezone.utc)
        )

    @staticmethod
    def _deal_position_identifier(deal: Any) -> int:
        return int(
            getattr(deal, "position_id", 0)
            or getattr(deal, "position", 0)
            or 0
        )

    def _history_deals(self, opened_at: datetime) -> list[Any]:
        if not hasattr(self.client, "history_deals_get"):
            return []
        return list(
            self.client.history_deals_get(
                opened_at - timedelta(minutes=10),
                datetime.now(timezone.utc),
            )
            or []
        )

    def _resolve_deal_group(
        self,
        stored: dict[str, Any],
    ) -> tuple[int, list[Any]] | None:
        opened_at = datetime.fromisoformat(str(stored["opened_at"])).astimezone(
            timezone.utc
        )
        deals = self._history_deals(opened_at)
        if not deals:
            return None

        symbol_names = {
            str(stored.get("symbol", "")).upper(),
            str(stored.get("broker_symbol", "")).upper(),
        }
        magic = int(
            stored.get("magic", 0)
            or MAGIC_BY_ENGINE.get(str(stored.get("engine", "")), 0)
            or 0
        )
        order_ticket = int(stored.get("order_ticket", 0) or stored.get("ticket", 0) or 0)
        deal_ticket = int(stored.get("deal_ticket", 0) or 0)
        identifier = int(stored.get("position_identifier", 0) or 0)

        filtered = [
            deal
            for deal in deals
            if str(getattr(deal, "symbol", "")).upper() in symbol_names
            and (magic <= 0 or int(getattr(deal, "magic", 0) or 0) == magic)
            and self._deal_time(deal) >= opened_at - timedelta(minutes=5)
        ]
        if not filtered:
            return None

        if identifier <= 0:
            direct = [
                deal
                for deal in filtered
                if int(getattr(deal, "order", 0) or 0) == order_ticket
                or int(getattr(deal, "ticket", 0) or 0) in {order_ticket, deal_ticket}
            ]
            if direct:
                identifier = self._deal_position_identifier(direct[0])

        if identifier <= 0:
            first = min(
                filtered,
                key=lambda deal: abs((self._deal_time(deal) - opened_at).total_seconds()),
            )
            identifier = self._deal_position_identifier(first)
        if identifier <= 0:
            return None

        group = [
            deal
            for deal in filtered
            if self._deal_position_identifier(deal) == identifier
        ]
        return (identifier, group) if group else None

    def _group_is_closed(self, identifier: int, group: list[Any]) -> bool:
        open_identifiers = {
            self._position_identifier(position) for position in self._positions()
        }
        if identifier in open_identifiers:
            return False
        exit_values = {
            int(getattr(self.client, "DEAL_ENTRY_OUT", 1)),
            int(getattr(self.client, "DEAL_ENTRY_INOUT", 2)),
            int(getattr(self.client, "DEAL_ENTRY_OUT_BY", 3)),
        }
        return any(
            int(getattr(deal, "entry", -1)) in exit_values
            or abs(float(getattr(deal, "profit", 0.0) or 0.0)) > 0
            for deal in group
        )

    @staticmethod
    def _group_pnl(group: list[Any]) -> float:
        return float(
            sum(
                float(getattr(deal, "profit", 0.0) or 0.0)
                + float(getattr(deal, "commission", 0.0) or 0.0)
                + float(getattr(deal, "swap", 0.0) or 0.0)
                + float(getattr(deal, "fee", 0.0) or 0.0)
                for deal in group
            )
        )

    def reconcile(self, now: datetime) -> None:
        del now
        open_positions = self._positions()
        by_ticket = {
            self._position_ticket(position): position for position in open_positions
        }
        by_identifier = {
            self._position_identifier(position): position for position in open_positions
        }

        for key, stored in list(self.state.data["positions"].items()):
            ticket = int(stored.get("position_ticket", 0) or stored.get("ticket", 0) or key)
            identifier = int(stored.get("position_identifier", 0) or 0)
            if ticket in by_ticket:
                current = by_ticket[ticket]
                stored["position_identifier"] = self._position_identifier(current)
                stored["position_ticket"] = ticket
                continue
            if identifier > 0 and identifier in by_identifier:
                current = by_identifier[identifier]
                actual_ticket = self._position_ticket(current)
                self.state.data["positions"].pop(key, None)
                stored["ticket"] = actual_ticket
                stored["position_ticket"] = actual_ticket
                self.state.data["positions"][str(actual_ticket)] = stored
                continue

            resolved = self._resolve_deal_group(stored)
            if resolved is None:
                continue
            closed_identifier, group = resolved
            if self.state.was_closed_processed(closed_identifier):
                self.state.data["positions"].pop(key, None)
                continue
            if not self._group_is_closed(closed_identifier, group):
                continue

            closed_at = max(self._deal_time(deal) for deal in group)
            stored["ticket"] = int(stored.get("ticket", 0) or key)
            pnl = self._group_pnl(group)
            self.state.record_closed(stored, pnl, closed_at)
            self.state.mark_closed_processed(closed_identifier, closed_at)

        self.state.save()
