"""Demo-only live executor matching the validated V14.3 research risk path.

This module ports the admission and sizing controls used by the enhanced
V12 + V14.3 chronological replay into the MT5 adapter.  It does not change
entry signals or promise that live results will reproduce the backtest.

The parity path includes:

* frozen GBPUSD/GBPJPY ICT setup risk tiers;
* satellite V12 and ICT risks supplied by the live signal adapters;
* symbol session, cluster, position, daily-loss and rolling-loss controls;
* 1.75% ICT admission-risk cap and 3.25% combined admission-risk cap;
* six simultaneous ICT positions and eight ICT entries per rolling hour;
* global six-loss pause, twelve-loss daily stop and symbol loss pressure;
* the continuous ICT drawdown scale from the profit-preserving profile;
* the enhanced 7.50/8.50/9.00/9.60% admission-preserving governor;
* demo-only AUTO transmission, order_check and broker-native lot sizing.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .v14_3_drawdown_governor import DrawdownGovernor
from .v14_3_live_execution import (
    EXECUTION_MODES,
    MAGIC_BY_ENGINE,
    AtomicLiveState,
    ExecutionResult,
    LiveRunnerConfig,
    LiveSignal,
    _successful_retcode,
    normalize_volume,
    pip_size,
)
from .v14_3_profit_preserving_profile import (
    PORTFOLIO_GUARD,
    SETUP_RISK_PERCENT,
    SYMBOL_GUARDS as GBP_SYMBOL_GUARDS,
    SymbolGuard,
)
from .v14_3_satellite_symbol_profile import SATELLITE_GUARDS

TRUTHY = {"1", "TRUE", "YES", "ON"}
PARITY_MAX_TRADE_RISK_PERCENT = 0.80
PARITY_MAX_COMBINED_OPEN_RISK_PERCENT = 3.25
PARITY_MAX_ICT_OPEN_RISK_PERCENT = 1.75
PARITY_MAX_SIMULTANEOUS_ICT_POSITIONS = 6
PARITY_MAX_TOTAL_ENTRIES_PER_HOUR = 8

PARITY_SYMBOL_GUARDS: dict[str, SymbolGuard] = {
    **GBP_SYMBOL_GUARDS,
    **SATELLITE_GUARDS,
}

PARITY_DRAWDOWN_GOVERNOR = DrawdownGovernor(
    soft_start_percent=7.50,
    medium_start_percent=8.50,
    defensive_start_percent=9.00,
    hard_stop_percent=9.60,
    soft_multiplier=0.98,
    medium_multiplier=0.82,
    defensive_multiplier=0.50,
    minimum_risk_percent=0.025,
)


@dataclass(frozen=True)
class ResearchParityLiveRunnerConfig(LiveRunnerConfig):
    """Fixed research-risk limits with environment-controlled execution gates."""

    max_live_risk_percent: float = PARITY_MAX_TRADE_RISK_PERCENT
    max_open_positions: int = 32  # Research admission is controlled by risk and ICT limits.
    max_open_risk_percent: float = PARITY_MAX_COMBINED_OPEN_RISK_PERCENT
    daily_account_loss_limit_percent: float = 100.0  # Research uses loss-count/symbol stops.
    live_hard_drawdown_percent: float = PARITY_DRAWDOWN_GOVERNOR.hard_stop_percent
    spread_caps: dict[str, float] = field(default_factory=lambda: {
        "GBPUSD": 2.0,
        "EURUSD": 1.5,
        "GBPJPY": 3.0,
        "AUDUSD": 1.8,
        "USDJPY": 2.0,
    })

    @classmethod
    def from_env(cls) -> "ResearchParityLiveRunnerConfig":
        mode = os.getenv("V14_3_EXECUTION_MODE", "READ_ONLY").strip().upper()
        config = cls(
            execution_mode=mode,
            state_path=os.getenv(
                "V14_3_LIVE_STATE_PATH",
                "state/v14_3_research_parity_live_state.json",
            ),
            forward_gate_passed=(
                os.getenv("V14_3_FORWARD_GATE_PASSED", "false").strip().upper()
                in TRUTHY
            ),
            allow_demo_auto=(
                os.getenv("V14_3_ALLOW_DEMO_AUTO", "false").strip().upper()
                in TRUTHY
            ),
            max_deviation_points=int(
                os.getenv("V14_3_MAX_DEVIATION_POINTS", "10")
            ),
            maximum_signal_age_minutes=int(
                os.getenv("V14_3_MAX_SIGNAL_AGE_MINUTES", "90")
            ),
            spread_caps={
                symbol: float(os.getenv(f"V14_3_MAX_SPREAD_{symbol}", default))
                for symbol, default in {
                    "GBPUSD": "2.0",
                    "EURUSD": "1.5",
                    "GBPJPY": "3.0",
                    "AUDUSD": "1.8",
                    "USDJPY": "2.0",
                }.items()
            },
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(
                f"V14_3_EXECUTION_MODE must be one of {sorted(EXECUTION_MODES)}"
            )
        if self.max_live_risk_percent != PARITY_MAX_TRADE_RISK_PERCENT:
            raise ValueError("Research parity trade-risk ceiling must remain 0.80%")
        if self.max_open_risk_percent != PARITY_MAX_COMBINED_OPEN_RISK_PERCENT:
            raise ValueError("Research parity combined open-risk cap must remain 3.25%")
        if self.live_hard_drawdown_percent != PARITY_DRAWDOWN_GOVERNOR.hard_stop_percent:
            raise ValueError("Research parity hard drawdown stop must remain 9.60%")
        if self.maximum_signal_age_minutes < 1:
            raise ValueError("Maximum signal age must be positive")


class ResearchParityState(AtomicLiveState):
    """Persist the chronological replay's daily and rolling control state."""

    def _default(self) -> dict[str, Any]:
        return {
            "seen": {},
            "positions": {},
            "peak_equity": 0.0,
            "day": self._new_day(None, 0.0),
        }

    @staticmethod
    def _new_day(date: str | None, equity: float) -> dict[str, Any]:
        return {
            "date": date,
            "start_equity": float(equity),
            "global_consecutive_losses": 0,
            "global_daily_losses": 0,
            "pause_until": None,
            "stop_day": False,
            "total_entries": [],
            "symbol_entries": {},
            "symbol_pnl": {},
            "symbol_losses": {},
            "symbol_consecutive_losses": {},
            "symbol_loss_pressure": {},
            "symbol_loss_times": {},
            "symbol_blocked": {},
            "symbol_cooldown_until": {},
        }

    def reset_day(self, now: datetime, equity: float) -> None:
        date = now.astimezone(timezone.utc).date().isoformat()
        if self.data["day"].get("date") != date:
            self.data["day"] = self._new_day(date, equity)
            self.save()

    def register_position(
        self,
        ticket: int,
        signal: LiveSignal,
        actual_risk_dollars: float,
        admission_risk_percent: float,
        executed_risk_percent: float,
        now: datetime,
    ) -> None:
        self.data["positions"][str(ticket)] = {
            "ticket": int(ticket),
            "symbol": signal.symbol,
            "broker_symbol": signal.broker_symbol,
            "engine": signal.engine,
            "setup": signal.setup,
            "mode": signal.mode,
            "side": signal.side,
            "risk_dollars": float(actual_risk_dollars),
            "admission_risk_percent": float(admission_risk_percent),
            "executed_risk_percent": float(executed_risk_percent),
            "opened_at": now.astimezone(timezone.utc).isoformat(),
        }
        self.save()

    def record_ict_entry(self, signal: LiveSignal) -> None:
        stamp = signal.signal_time.astimezone(timezone.utc).isoformat()
        day = self.data["day"]
        day["total_entries"].append(stamp)
        day["symbol_entries"].setdefault(signal.symbol, []).append(stamp)
        self.save()

    def record_closed(
        self,
        position: dict[str, Any],
        pnl: float,
        closed_at: datetime,
    ) -> None:
        self.reset_day(closed_at, float(self.data["day"].get("start_equity", 0.0)))
        self.data["positions"].pop(str(position["ticket"]), None)
        if str(position.get("mode", "")).upper() != "ICT":
            self.save()
            return

        symbol = str(position["symbol"]).upper()
        guard = PARITY_SYMBOL_GUARDS[symbol]
        day = self.data["day"]
        day["symbol_pnl"][symbol] = (
            float(day["symbol_pnl"].get(symbol, 0.0)) + float(pnl)
        )

        if pnl < 0:
            day["global_consecutive_losses"] = int(
                day.get("global_consecutive_losses", 0)
            ) + 1
            day["global_daily_losses"] = int(day.get("global_daily_losses", 0)) + 1
            day["symbol_losses"][symbol] = int(
                day["symbol_losses"].get(symbol, 0)
            ) + 1
            day["symbol_consecutive_losses"][symbol] = int(
                day["symbol_consecutive_losses"].get(symbol, 0)
            ) + 1
            day["symbol_loss_pressure"][symbol] = float(
                day["symbol_loss_pressure"].get(symbol, 0.0)
            ) + 1.0
            loss_times = day["symbol_loss_times"].setdefault(symbol, [])
            loss_times.append(closed_at.astimezone(timezone.utc).isoformat())
            cutoff = closed_at - timedelta(hours=guard.rolling_loss_hours)
            day["symbol_loss_times"][symbol] = [
                value
                for value in loss_times
                if datetime.fromisoformat(value).astimezone(timezone.utc) >= cutoff
            ]

            if (
                day["global_consecutive_losses"]
                >= PORTFOLIO_GUARD.global_pause_after_consecutive_losses
            ):
                day["pause_until"] = (
                    closed_at
                    + timedelta(hours=PORTFOLIO_GUARD.global_pause_hours)
                ).astimezone(timezone.utc).isoformat()
            if (
                day["global_daily_losses"]
                >= PORTFOLIO_GUARD.global_stop_after_daily_losses
            ):
                day["stop_day"] = True
            if (
                day["symbol_consecutive_losses"][symbol]
                >= guard.block_after_consecutive_losses
            ):
                day["symbol_blocked"][symbol] = True
            if len(day["symbol_loss_times"][symbol]) >= guard.rolling_loss_count:
                day["symbol_cooldown_until"][symbol] = (
                    closed_at + timedelta(hours=guard.rolling_loss_hours)
                ).astimezone(timezone.utc).isoformat()
            if day["symbol_losses"][symbol] >= guard.stop_after_daily_losses:
                day["symbol_blocked"][symbol] = True
        elif pnl > 0:
            day["global_consecutive_losses"] = 0
            day["symbol_consecutive_losses"][symbol] = 0
            day["symbol_loss_pressure"][symbol] = max(
                0.0,
                float(day["symbol_loss_pressure"].get(symbol, 0.0))
                - guard.win_pressure_recovery,
            )

        start_equity = float(day.get("start_equity", 0.0) or 0.0)
        limit = -(guard.daily_loss_cap_percent / 100.0) * start_equity
        if float(day["symbol_pnl"].get(symbol, 0.0)) <= limit:
            day["symbol_blocked"][symbol] = True
        self.save()


class ResearchParityLiveExecutor:
    """Execute signals through the exact enhanced research admission path."""

    def __init__(
        self,
        client: Any,
        config: ResearchParityLiveRunnerConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> None:
        self.client = client
        self.config = config
        self.state = ResearchParityState(config.state_path)
        self.approval_callback = approval_callback or self._console_approval
        self.governor = PARITY_DRAWDOWN_GOVERNOR

    @staticmethod
    def _console_approval(proposal: dict[str, Any]) -> bool:
        print(json.dumps({"approval_required": proposal}, indent=2, default=str))
        return input("Type exactly YES to place this DEMO order: ").strip() == "YES"

    def _positions(self) -> list[Any]:
        return list(self.client.positions_get() or [])

    def _is_demo(self, account: Any) -> bool:
        return getattr(account, "trade_mode", None) == getattr(
            self.client,
            "ACCOUNT_TRADE_MODE_DEMO",
            0,
        )

    def _position_risk_dollars(self, position: Any) -> float:
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        stop = float(getattr(position, "sl", 0.0) or 0.0)
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        if entry <= 0 or stop <= 0 or volume <= 0:
            return 0.0
        order_type = (
            self.client.ORDER_TYPE_BUY
            if int(position.type) == int(self.client.POSITION_TYPE_BUY)
            else self.client.ORDER_TYPE_SELL
        )
        result = self.client.order_calc_profit(
            order_type,
            position.symbol,
            volume,
            entry,
            stop,
        )
        return abs(float(result)) if result is not None else 0.0

    def _closed_position_result(
        self,
        ticket: int,
        opened_at: str,
    ) -> tuple[float, datetime] | None:
        if not hasattr(self.client, "history_deals_get"):
            return None
        start = datetime.fromisoformat(opened_at).astimezone(timezone.utc) - timedelta(days=1)
        deals = self.client.history_deals_get(
            start,
            datetime.now(timezone.utc),
            position=ticket,
        ) or []
        if not deals:
            return None
        pnl = sum(
            float(getattr(deal, "profit", 0.0) or 0.0)
            + float(getattr(deal, "commission", 0.0) or 0.0)
            + float(getattr(deal, "swap", 0.0) or 0.0)
            + float(getattr(deal, "fee", 0.0) or 0.0)
            for deal in deals
        )
        timestamps = [
            int(getattr(deal, "time", 0) or 0)
            for deal in deals
            if int(getattr(deal, "time", 0) or 0) > 0
        ]
        closed_at = (
            datetime.fromtimestamp(max(timestamps), tz=timezone.utc)
            if timestamps
            else datetime.now(timezone.utc)
        )
        return float(pnl), closed_at

    def reconcile(self, now: datetime) -> None:
        del now
        open_tickets = {int(position.ticket) for position in self._positions()}
        for ticket, stored in list(self.state.data["positions"].items()):
            if int(ticket) in open_tickets:
                continue
            result = self._closed_position_result(
                int(ticket),
                str(stored["opened_at"]),
            )
            if result is not None:
                pnl, closed_at = result
                self.state.record_closed(stored, pnl, closed_at)

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value).astimezone(timezone.utc)

    def _prune_entries(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=1)
        day = self.state.data["day"]
        day["total_entries"] = [
            value
            for value in day.get("total_entries", [])
            if datetime.fromisoformat(value).astimezone(timezone.utc) >= cutoff
        ]
        symbol_entries = day.get("symbol_entries", {})
        for symbol, values in list(symbol_entries.items()):
            symbol_entries[symbol] = [
                value
                for value in values
                if datetime.fromisoformat(value).astimezone(timezone.utc) >= cutoff
            ]

    def _tracked_open_positions(self) -> list[dict[str, Any]]:
        tickets = {int(position.ticket) for position in self._positions()}
        return [
            dict(value)
            for key, value in self.state.data["positions"].items()
            if int(key) in tickets
        ]

    def _admission_open_risk(self, account: Any) -> tuple[float, float]:
        balance = float(getattr(account, "balance", 0.0) or 0.0)
        tracked = {
            int(value["ticket"]): value
            for value in self._tracked_open_positions()
        }
        combined = 0.0
        ict = 0.0
        for position in self._positions():
            stored = tracked.get(int(position.ticket))
            if stored is not None:
                risk = float(
                    stored.get("admission_risk_percent")
                    or stored.get("executed_risk_percent")
                    or 0.0
                )
                mode = str(stored.get("mode", "")).upper()
            else:
                dollars = self._position_risk_dollars(position)
                risk = dollars / balance * 100.0 if balance > 0 else 0.0
                mode = "EXTERNAL"
            combined += risk
            if mode == "ICT":
                ict += risk
        return combined, ict

    def _symbol_guard_reason(
        self,
        signal: LiveSignal,
        account: Any,
        now: datetime,
    ) -> Optional[str]:
        if signal.mode.upper() != "ICT":
            return None
        symbol = signal.symbol.upper()
        guard = PARITY_SYMBOL_GUARDS[symbol]
        entry_time = signal.signal_time.astimezone(timezone.utc)
        self._prune_entries(entry_time)
        day = self.state.data["day"]

        if not (
            guard.session_start_hour_utc
            <= entry_time.hour
            < guard.session_end_hour_utc
        ):
            return "SYMBOL_SESSION_BLOCK"
        drawdown = self.state.drawdown_percent(float(account.equity))
        if drawdown >= PORTFOLIO_GUARD.hard_drawdown_stop_percent:
            return "HARD_DRAWDOWN_STOP"
        if bool(day.get("stop_day", False)):
            return "GLOBAL_DAILY_LOSS_STOP"
        pause_until = self._parse_time(day.get("pause_until"))
        if pause_until is not None and now < pause_until:
            return "GLOBAL_CONSECUTIVE_LOSS_PAUSE"
        if bool(day.get("symbol_blocked", {}).get(symbol, False)):
            return "SYMBOL_BLOCK_REST_DAY"
        cooldown = self._parse_time(
            day.get("symbol_cooldown_until", {}).get(symbol)
        )
        if cooldown is not None and now < cooldown:
            return "SYMBOL_ROLLING_LOSS_COOLDOWN"
        if len(day.get("symbol_entries", {}).get(symbol, [])) >= guard.max_entries_per_hour:
            return "TRADE_CLUSTER_SYMBOL_HOUR"
        if len(day.get("total_entries", [])) >= PARITY_MAX_TOTAL_ENTRIES_PER_HOUR:
            return "TRADE_CLUSTER_TOTAL_HOUR"

        tracked = self._tracked_open_positions()
        open_symbol = sum(
            str(item.get("mode", "")).upper() == "ICT"
            and str(item.get("symbol", "")).upper() == symbol
            for item in tracked
        )
        if open_symbol >= guard.max_open_positions:
            return "SYMBOL_OPEN_POSITION_LIMIT"
        open_ict = sum(
            str(item.get("mode", "")).upper() == "ICT"
            for item in tracked
        )
        if open_ict >= PARITY_MAX_SIMULTANEOUS_ICT_POSITIONS:
            return "MAX_SIMULTANEOUS_ICT_POSITIONS"
        return None

    def _under_loss_pressure(self, symbol: str) -> bool:
        day = self.state.data["day"]
        return bool(
            int(day.get("global_consecutive_losses", 0)) > 0
            or float(day.get("symbol_loss_pressure", {}).get(symbol, 0.0)) > 0
            or float(day.get("symbol_pnl", {}).get(symbol, 0.0)) < 0
        )

    def _ict_admission_risk(
        self,
        signal: LiveSignal,
        drawdown_percent: float,
    ) -> float:
        symbol = signal.symbol.upper()
        setup = signal.setup
        base = float(
            SETUP_RISK_PERCENT.get(
                (symbol, setup),
                signal.requested_risk_percent,
            )
        )
        guard = PARITY_SYMBOL_GUARDS[symbol]
        if self._under_loss_pressure(symbol):
            base *= guard.post_loss_multiplier

        drawdown = max(0.0, float(drawdown_percent))
        start = PORTFOLIO_GUARD.drawdown_scale_start_percent
        end = PORTFOLIO_GUARD.drawdown_scale_end_percent
        floor = min(base, PORTFOLIO_GUARD.drawdown_risk_floor_percent)
        if drawdown <= start:
            return base
        if drawdown >= end:
            return floor
        fraction = (drawdown - start) / (end - start)
        return base * (1.0 - fraction) + floor * fraction

    def place(
        self,
        signal: LiveSignal,
        now: Optional[datetime] = None,
    ) -> ExecutionResult:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        account = self.client.account_info()
        if account is None:
            return ExecutionResult(
                False,
                "ACCOUNT_UNAVAILABLE",
                "MT5 account information is unavailable",
            )
        self.state.reset_day(now, float(account.equity))
        self.state.update_equity(float(account.equity))
        self.reconcile(now)

        if signal.key in self.state.data["seen"]:
            return ExecutionResult(False, "DUPLICATE_SIGNAL", "Signal was already processed")
        age = now - signal.signal_time.astimezone(timezone.utc)
        if age > timedelta(minutes=self.config.maximum_signal_age_minutes) or age < timedelta(minutes=-5):
            return ExecutionResult(False, "STALE_SIGNAL", f"Signal age is {age}")

        drawdown = self.state.drawdown_percent(float(account.equity))
        if drawdown >= self.governor.hard_stop_percent:
            return ExecutionResult(
                False,
                "DRAWDOWN_GOVERNOR_HARD_STOP",
                f"Live drawdown {drawdown:.2f}% reached the 9.60% parity stop",
            )

        guard_reason = self._symbol_guard_reason(signal, account, now)
        if guard_reason:
            return ExecutionResult(
                False,
                guard_reason,
                "Research parity admission guard rejected the signal",
            )

        if signal.mode.upper() == "ICT":
            requested = self._ict_admission_risk(signal, drawdown)
            combined_open, ict_open = self._admission_open_risk(account)
            if ict_open + requested > PARITY_MAX_ICT_OPEN_RISK_PERCENT + 1e-12:
                return ExecutionResult(
                    False,
                    "ICT_OPEN_RISK_CAP",
                    "ICT admission risk would exceed 1.75%",
                )
            if combined_open + requested > PARITY_MAX_COMBINED_OPEN_RISK_PERCENT + 1e-12:
                return ExecutionResult(
                    False,
                    "COMBINED_OPEN_RISK_CAP",
                    "Combined admission risk would exceed 3.25%",
                )
        else:
            requested = float(signal.requested_risk_percent)

        if requested <= 0 or requested > PARITY_MAX_TRADE_RISK_PERCENT + 1e-12:
            return ExecutionResult(
                False,
                "UNAPPROVED_RISK_TIER",
                f"Requested parity risk {requested:.3f}% is outside (0, 0.80%]",
            )

        governed = self.governor.apply(requested, drawdown)
        if governed <= 0:
            return ExecutionResult(
                False,
                "DRAWDOWN_GOVERNOR_HARD_STOP",
                "Enhanced drawdown governor returned zero risk",
            )

        info = self.client.symbol_info(signal.broker_symbol)
        tick = self.client.symbol_info_tick(signal.broker_symbol)
        if info is None or tick is None:
            return ExecutionResult(
                False,
                "MARKET_DATA_UNAVAILABLE",
                "Symbol information or tick is unavailable",
            )
        pip = pip_size(info, signal.symbol)
        spread = (float(tick.ask) - float(tick.bid)) / pip
        if spread > self.config.spread_caps[signal.symbol]:
            return ExecutionResult(
                False,
                "SPREAD_TOO_WIDE",
                f"Spread {spread:.2f} pips exceeds {self.config.spread_caps[signal.symbol]:.2f}",
            )

        is_buy = signal.side.upper() == "BUY"
        order_type = self.client.ORDER_TYPE_BUY if is_buy else self.client.ORDER_TYPE_SELL
        price = float(tick.ask if is_buy else tick.bid)
        stop = (
            price - signal.stop_pips * pip
            if is_buy
            else price + signal.stop_pips * pip
        )
        target = (
            price + signal.target_pips * pip
            if is_buy
            else price - signal.target_pips * pip
        )
        minimum_stop = float(getattr(info, "trade_stops_level", 0.0) or 0.0) * float(
            getattr(info, "point", 0.0) or 0.0
        )
        if (
            abs(price - stop) + 1e-12 < minimum_stop
            or abs(target - price) + 1e-12 < minimum_stop
        ):
            return ExecutionResult(
                False,
                "BROKER_STOP_DISTANCE",
                "SL or TP is inside the broker minimum stop distance",
            )

        balance = float(account.balance)
        intended_dollars = balance * governed / 100.0
        one_lot_loss = self.client.order_calc_profit(
            order_type,
            signal.broker_symbol,
            1.0,
            price,
            stop,
        )
        if one_lot_loss is None or abs(float(one_lot_loss)) <= 0:
            return ExecutionResult(
                False,
                "BROKER_SIZING_UNAVAILABLE",
                "order_calc_profit could not determine one-lot stop risk",
            )
        volume = normalize_volume(info, intended_dollars / abs(float(one_lot_loss)))
        if volume <= 0:
            return ExecutionResult(
                False,
                "VOLUME_TOO_SMALL",
                "Risk-based volume is below the broker minimum",
            )
        actual_risk_dollars = abs(
            float(
                self.client.order_calc_profit(
                    order_type,
                    signal.broker_symbol,
                    volume,
                    price,
                    stop,
                )
            )
        )
        actual_risk_percent = (
            actual_risk_dollars / balance * 100.0 if balance > 0 else 0.0
        )
        if actual_risk_percent > governed + 1e-9:
            return ExecutionResult(
                False,
                "RISK_ROUNDING_EXCEEDED",
                "Normalized volume exceeded the governed research risk",
            )

        digits = int(getattr(info, "digits", 5) or 5)
        request = {
            "action": self.client.TRADE_ACTION_DEAL,
            "symbol": signal.broker_symbol,
            "volume": volume,
            "type": order_type,
            "price": round(price, digits),
            "sl": round(stop, digits),
            "tp": round(target, digits),
            "deviation": self.config.max_deviation_points,
            "magic": MAGIC_BY_ENGINE.get(signal.engine, 20264399),
            "comment": f"V143P {signal.mode} {signal.engine}"[:31],
            "type_time": self.client.ORDER_TIME_GTC,
            "type_filling": int(
                getattr(
                    info,
                    "filling_mode",
                    getattr(self.client, "ORDER_FILLING_IOC", 1),
                )
            ),
        }
        proposal = {
            "signal": asdict(signal),
            "mode": self.config.execution_mode,
            "profile": "V14_3_ENHANCED_RESEARCH_RISK_PARITY",
            "account_login": getattr(account, "login", None),
            "account_server": str(getattr(account, "server", "")),
            "is_demo": self._is_demo(account),
            "volume": volume,
            "base_or_signal_risk_percent": float(signal.requested_risk_percent),
            "admission_risk_percent": requested,
            "governed_risk_percent": governed,
            "actual_risk_percent": actual_risk_percent,
            "spread_pips": spread,
            "drawdown_percent": drawdown,
            "request": request,
        }

        if self.config.execution_mode == "READ_ONLY":
            self.state.mark_seen(signal.key, now)
            return ExecutionResult(
                True,
                "READ_ONLY_PROPOSAL",
                "Validated research-parity proposal only; no broker order was sent",
                volume=volume,
                risk_percent=actual_risk_percent,
                proposal=proposal,
            )
        if not self._is_demo(account):
            return ExecutionResult(
                False,
                "DEMO_ACCOUNT_REQUIRED",
                "Research-risk parity transmission is restricted to MT5 demo accounts",
                proposal=proposal,
            )
        if (
            self.config.execution_mode == "APPROVAL"
            and not self.approval_callback(proposal)
        ):
            return ExecutionResult(
                False,
                "APPROVAL_DECLINED",
                "Exact YES approval was not received",
                proposal=proposal,
            )
        if self.config.execution_mode == "AUTO" and not (
            self.config.allow_demo_auto and self.config.forward_gate_passed
        ):
            return ExecutionResult(
                False,
                "AUTO_GATE_CLOSED",
                "AUTO requires both demo-auto and forward-validation gates",
                proposal=proposal,
            )

        check = self.client.order_check(request)
        if check is None:
            return ExecutionResult(
                False,
                "ORDER_CHECK_NONE",
                f"order_check returned None: {self.client.last_error()}",
                proposal=proposal,
            )
        check_retcode = getattr(check, "retcode", None)
        if check_retcode not in {
            0,
            getattr(self.client, "TRADE_RETCODE_DONE", None),
        }:
            return ExecutionResult(
                False,
                "ORDER_CHECK_REJECTED",
                f"order_check rejected request: {check_retcode} {getattr(check, 'comment', '')}",
                proposal=proposal,
            )

        self.state.mark_seen(signal.key, now)
        result = self.client.order_send(request)
        if result is None:
            return ExecutionResult(
                False,
                "ORDER_SEND_NONE",
                f"order_send returned None: {self.client.last_error()}",
                proposal=proposal,
            )
        if not _successful_retcode(self.client, getattr(result, "retcode", None)):
            return ExecutionResult(
                False,
                "ORDER_REJECTED",
                f"MT5 rejected order: {getattr(result, 'retcode', None)} {getattr(result, 'comment', '')}",
                proposal=proposal,
            )
        ticket = int(getattr(result, "order", 0) or getattr(result, "deal", 0) or 0)
        if ticket <= 0:
            positions_after = self.client.positions_get(symbol=signal.broker_symbol) or []
            matching = [
                position
                for position in positions_after
                if int(getattr(position, "magic", 0) or 0) == request["magic"]
            ]
            ticket = int(matching[-1].ticket) if matching else 0
        if ticket <= 0:
            return ExecutionResult(
                False,
                "TICKET_UNAVAILABLE",
                "MT5 accepted the request but no ticket was recoverable",
                volume=volume,
                risk_percent=actual_risk_percent,
                proposal=proposal,
            )

        self.state.register_position(
            ticket,
            signal,
            actual_risk_dollars,
            requested,
            governed,
            now,
        )
        if signal.mode.upper() == "ICT":
            self.state.record_ict_entry(signal)
        return ExecutionResult(
            True,
            "ORDER_FILLED",
            "Demo order checked, sent and persisted through research-risk parity controls",
            ticket=ticket,
            volume=volume,
            risk_percent=actual_risk_percent,
            proposal=proposal,
        )


def parity_profile_snapshot() -> dict[str, Any]:
    """Return the immutable profile for preflight and dashboard diagnostics."""
    return {
        "setup_risk_percent": {
            f"{symbol}/{setup}": risk
            for (symbol, setup), risk in sorted(SETUP_RISK_PERCENT.items())
            if symbol in {"GBPUSD", "GBPJPY"}
        },
        "satellite_symbol_guards": {
            symbol: asdict(guard)
            for symbol, guard in sorted(PARITY_SYMBOL_GUARDS.items())
        },
        "portfolio_guard": {
            "max_ict_open_risk_percent": PARITY_MAX_ICT_OPEN_RISK_PERCENT,
            "max_combined_open_risk_percent": PARITY_MAX_COMBINED_OPEN_RISK_PERCENT,
            "max_simultaneous_ict_positions": PARITY_MAX_SIMULTANEOUS_ICT_POSITIONS,
            "max_total_entries_per_hour": PARITY_MAX_TOTAL_ENTRIES_PER_HOUR,
            "global_pause_after_consecutive_losses": PORTFOLIO_GUARD.global_pause_after_consecutive_losses,
            "global_pause_hours": PORTFOLIO_GUARD.global_pause_hours,
            "global_stop_after_daily_losses": PORTFOLIO_GUARD.global_stop_after_daily_losses,
            "continuous_ict_drawdown_scale_start_percent": PORTFOLIO_GUARD.drawdown_scale_start_percent,
            "continuous_ict_drawdown_scale_end_percent": PORTFOLIO_GUARD.drawdown_scale_end_percent,
            "continuous_ict_drawdown_floor_percent": PORTFOLIO_GUARD.drawdown_risk_floor_percent,
        },
        "admission_governor": asdict(PARITY_DRAWDOWN_GOVERNOR),
        "demo_only": True,
    }
