"""Fail-closed MT5 execution for the V12 + V14.3 satellite portfolio.

The default mode is READ_ONLY. APPROVAL requires an exact ``YES`` response.
AUTO is permitted only on a confirmed MT5 demo account when both the forward
validation and explicit AUTO environment gates are enabled. This module is
broker-adapter code, not a promise of future profitability.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .v14_3_drawdown_governor import DrawdownGovernor
from .v14_3_satellite_symbol_profile import SATELLITE_GUARDS


EXECUTION_MODES = {"READ_ONLY", "APPROVAL", "AUTO"}
MAGIC_BY_ENGINE = {
    "GBPUSD_V10_PRECISION": 20264301,
    "GBPUSD_SWING_RETEST": 20264302,
    "EURUSD_SWING_CORE": 20264303,
    "EURUSD_SWING_RETEST": 20264304,
    "GBPJPY_SWING_CORE": 20264305,
    "AUDUSD_TREND_PULLBACK": 20264306,
    "USDJPY_SAFE_HAVEN_BREAKOUT": 20264307,
    "EURUSD_ICT_LIQUIDITY": 20264321,
    "AUDUSD_ICT_ASIA_LONDON": 20264322,
    "USDJPY_ICT_SESSION_SWEEP": 20264323,
    "ICT_V14_3_GBPUSD": 20264331,
    "ICT_V14_3_GBPJPY": 20264332,
    "ICT_V14_3_UNDER10": 20264333,
}


@dataclass(frozen=True)
class LiveSignal:
    symbol: str
    broker_symbol: str
    engine: str
    setup: str
    mode: str
    side: str
    signal_time: datetime
    requested_risk_percent: float
    stop_pips: float
    target_pips: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.symbol.upper() not in {"GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"}:
            raise ValueError(f"Unsupported symbol: {self.symbol}")
        if self.side.upper() not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.mode.upper() not in {"V12", "ICT"}:
            raise ValueError("mode must be V12 or ICT")
        if self.signal_time.tzinfo is None:
            raise ValueError("signal_time must be timezone-aware")
        if self.requested_risk_percent <= 0 or self.stop_pips <= 0 or self.target_pips <= 0:
            raise ValueError("risk, stop and target must be positive")

    @property
    def key(self) -> str:
        stamp = self.signal_time.astimezone(timezone.utc).isoformat()
        return f"{self.symbol}:{self.engine}:{self.setup}:{self.side.upper()}:{stamp}"


@dataclass(frozen=True)
class LiveRunnerConfig:
    execution_mode: str = "READ_ONLY"
    state_path: str = "state/v14_3_satellite_live_state.json"
    max_live_risk_percent: float = 0.25
    forward_gate_passed: bool = False
    allow_demo_auto: bool = False
    max_open_positions: int = 5
    max_open_risk_percent: float = 1.50
    daily_account_loss_limit_percent: float = 4.00
    live_hard_drawdown_percent: float = 6.00
    max_deviation_points: int = 10
    maximum_signal_age_minutes: int = 90
    spread_caps: dict[str, float] = field(default_factory=lambda: {
        "GBPUSD": 2.0,
        "EURUSD": 1.5,
        "GBPJPY": 3.0,
        "AUDUSD": 1.8,
        "USDJPY": 2.0,
    })

    @classmethod
    def from_env(cls) -> "LiveRunnerConfig":
        truthy = {"1", "TRUE", "YES", "ON"}
        mode = os.getenv("V14_3_EXECUTION_MODE", "READ_ONLY").strip().upper()
        config = cls(
            execution_mode=mode,
            state_path=os.getenv("V14_3_LIVE_STATE_PATH", "state/v14_3_satellite_live_state.json"),
            max_live_risk_percent=float(os.getenv("V14_3_LIVE_MAX_RISK_PERCENT", "0.25")),
            forward_gate_passed=os.getenv("V14_3_FORWARD_GATE_PASSED", "false").strip().upper() in truthy,
            allow_demo_auto=os.getenv("V14_3_ALLOW_DEMO_AUTO", "false").strip().upper() in truthy,
            max_open_positions=int(os.getenv("V14_3_MAX_OPEN_POSITIONS", "5")),
            max_open_risk_percent=float(os.getenv("V14_3_MAX_OPEN_RISK_PERCENT", "1.50")),
            daily_account_loss_limit_percent=float(os.getenv("V14_3_DAILY_LOSS_LIMIT_PERCENT", "4.00")),
            live_hard_drawdown_percent=float(os.getenv("V14_3_LIVE_HARD_DD_PERCENT", "6.00")),
            max_deviation_points=int(os.getenv("V14_3_MAX_DEVIATION_POINTS", "10")),
            maximum_signal_age_minutes=int(os.getenv("V14_3_MAX_SIGNAL_AGE_MINUTES", "90")),
            spread_caps={
                symbol: float(os.getenv(f"V14_3_MAX_SPREAD_{symbol}", default))
                for symbol, default in {
                    "GBPUSD": "2.0", "EURUSD": "1.5", "GBPJPY": "3.0",
                    "AUDUSD": "1.8", "USDJPY": "2.0",
                }.items()
            },
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(f"V14_3_EXECUTION_MODE must be one of {sorted(EXECUTION_MODES)}")
        if not 0 < self.max_live_risk_percent <= 0.45:
            raise ValueError("V14_3_LIVE_MAX_RISK_PERCENT must be within (0, 0.45]")
        if not self.forward_gate_passed and self.max_live_risk_percent > 0.25:
            raise ValueError("Risk above 0.25% requires V14_3_FORWARD_GATE_PASSED=true")
        if not 0 < self.max_open_risk_percent <= 3.25:
            raise ValueError("Invalid maximum open-risk percentage")
        if not 0 < self.live_hard_drawdown_percent < 9.60:
            raise ValueError("Live hard drawdown must be positive and below the research 9.60% stop")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be positive")


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    code: str
    message: str
    ticket: Optional[int] = None
    volume: float = 0.0
    risk_percent: float = 0.0
    proposal: Optional[dict[str, Any]] = None


class AtomicLiveState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data = self._load()

    def _default(self) -> dict[str, Any]:
        return {
            "seen": {},
            "positions": {},
            "peak_equity": 0.0,
            "day": {
                "date": None,
                "start_equity": 0.0,
                "symbol_pnl": {},
                "symbol_losses": {},
                "symbol_consecutive_losses": {},
                "symbol_cooldown_until": {},
            },
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._default()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return self._default()
        default = self._default()
        for key, value in default.items():
            payload.setdefault(key, value)
        for key, value in default["day"].items():
            payload["day"].setdefault(key, value)
        return payload

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(self.data, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temp.replace(self.path)

    def reset_day(self, now: datetime, equity: float) -> None:
        date = now.astimezone(timezone.utc).date().isoformat()
        if self.data["day"].get("date") != date:
            self.data["day"] = {
                "date": date,
                "start_equity": float(equity),
                "symbol_pnl": {},
                "symbol_losses": {},
                "symbol_consecutive_losses": {},
                "symbol_cooldown_until": {},
            }
            self.save()

    def update_equity(self, equity: float) -> None:
        self.data["peak_equity"] = max(float(self.data.get("peak_equity", 0.0)), float(equity))
        self.save()

    def drawdown_percent(self, equity: float) -> float:
        peak = float(self.data.get("peak_equity", 0.0))
        return max(0.0, (peak - equity) / peak * 100.0) if peak > 0 else 0.0

    def mark_seen(self, key: str, now: datetime) -> None:
        self.data["seen"][key] = now.astimezone(timezone.utc).isoformat()
        if len(self.data["seen"]) > 10000:
            ordered = sorted(self.data["seen"].items(), key=lambda item: item[1])[-8000:]
            self.data["seen"] = dict(ordered)
        self.save()

    def register_position(self, ticket: int, signal: LiveSignal, risk_dollars: float, now: datetime) -> None:
        self.data["positions"][str(ticket)] = {
            "ticket": int(ticket),
            "symbol": signal.symbol,
            "broker_symbol": signal.broker_symbol,
            "engine": signal.engine,
            "mode": signal.mode,
            "risk_dollars": float(risk_dollars),
            "opened_at": now.astimezone(timezone.utc).isoformat(),
        }
        self.save()

    def record_closed(self, position: dict[str, Any], pnl: float, now: datetime) -> None:
        symbol = str(position["symbol"])
        day = self.data["day"]
        day["symbol_pnl"][symbol] = float(day["symbol_pnl"].get(symbol, 0.0)) + float(pnl)
        if pnl < 0:
            day["symbol_losses"][symbol] = int(day["symbol_losses"].get(symbol, 0)) + 1
            day["symbol_consecutive_losses"][symbol] = int(day["symbol_consecutive_losses"].get(symbol, 0)) + 1
            guard = SATELLITE_GUARDS.get(symbol)
            if guard and day["symbol_consecutive_losses"][symbol] >= guard.rolling_loss_count:
                until = now + timedelta(hours=guard.rolling_loss_hours)
                day["symbol_cooldown_until"][symbol] = until.astimezone(timezone.utc).isoformat()
        elif pnl > 0:
            day["symbol_consecutive_losses"][symbol] = 0
        self.data["positions"].pop(str(position["ticket"]), None)
        self.save()


def resolve_broker_symbol(client: Any, canonical: str) -> str:
    canonical = canonical.upper()
    direct = client.symbol_info(canonical)
    if direct is not None:
        if not bool(getattr(direct, "visible", True)):
            client.symbol_select(canonical, True)
        return canonical
    matches: list[str] = []
    for item in client.symbols_get() or []:
        name = str(getattr(item, "name", item))
        compact = "".join(character for character in name.upper() if character.isalpha())
        if canonical in compact:
            matches.append(name)
    if not matches:
        raise RuntimeError(f"Broker symbol not found for {canonical}")
    matches.sort(key=lambda value: (len(value), value))
    selected = matches[0]
    if not client.symbol_select(selected, True):
        raise RuntimeError(f"Unable to select broker symbol {selected}")
    return selected


def pip_size(info: Any, symbol: str) -> float:
    point = float(getattr(info, "point", 0.0) or 0.0)
    digits = int(getattr(info, "digits", 0) or 0)
    if point > 0:
        return point * 10 if digits in {3, 5} else point
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def normalize_volume(info: Any, raw: float) -> float:
    minimum = float(getattr(info, "volume_min", 0.01) or 0.01)
    maximum = float(getattr(info, "volume_max", raw) or raw)
    step = float(getattr(info, "volume_step", 0.01) or 0.01)
    if raw < minimum - 1e-12:
        return 0.0
    clipped = min(float(raw), maximum)
    steps = math.floor((clipped + 1e-12) / step)
    value = steps * step
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return round(value, decimals) if value >= minimum - 1e-12 else 0.0


def _successful_retcode(client: Any, retcode: Any) -> bool:
    values = {getattr(client, "TRADE_RETCODE_DONE", None), getattr(client, "TRADE_RETCODE_PLACED", None), getattr(client, "TRADE_RETCODE_DONE_PARTIAL", None)}
    return retcode in {value for value in values if value is not None}


class SatelliteLiveExecutor:
    def __init__(
        self,
        client: Any,
        config: LiveRunnerConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> None:
        self.client = client
        self.config = config
        self.state = AtomicLiveState(config.state_path)
        self.approval_callback = approval_callback or self._console_approval
        self.governor = DrawdownGovernor(
            soft_start_percent=7.50,
            medium_start_percent=8.50,
            defensive_start_percent=9.00,
            hard_stop_percent=9.60,
            soft_multiplier=0.98,
            medium_multiplier=0.82,
            defensive_multiplier=0.50,
            minimum_risk_percent=0.025,
        )

    @staticmethod
    def _console_approval(proposal: dict[str, Any]) -> bool:
        print(json.dumps({"approval_required": proposal}, indent=2, default=str))
        return input("Type exactly YES to place this DEMO order: ").strip() == "YES"

    def _positions(self) -> list[Any]:
        return list(self.client.positions_get() or [])

    def _is_demo(self, account: Any) -> bool:
        return getattr(account, "trade_mode", None) == getattr(self.client, "ACCOUNT_TRADE_MODE_DEMO", 0)

    def _position_risk_dollars(self, position: Any) -> float:
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        stop = float(getattr(position, "sl", 0.0) or 0.0)
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        if entry <= 0 or stop <= 0 or volume <= 0:
            return 0.0
        order_type = self.client.ORDER_TYPE_BUY if int(position.type) == int(self.client.POSITION_TYPE_BUY) else self.client.ORDER_TYPE_SELL
        result = self.client.order_calc_profit(order_type, position.symbol, volume, entry, stop)
        return abs(float(result)) if result is not None else 0.0

    def _closed_position_pnl(self, ticket: int, opened_at: str) -> Optional[float]:
        if not hasattr(self.client, "history_deals_get"):
            return None
        start = datetime.fromisoformat(opened_at).astimezone(timezone.utc) - timedelta(days=1)
        deals = self.client.history_deals_get(start, datetime.now(timezone.utc), position=ticket) or []
        if not deals:
            return None
        return sum(
            float(getattr(deal, "profit", 0.0) or 0.0)
            + float(getattr(deal, "commission", 0.0) or 0.0)
            + float(getattr(deal, "swap", 0.0) or 0.0)
            + float(getattr(deal, "fee", 0.0) or 0.0)
            for deal in deals
        )

    def reconcile(self, now: datetime) -> None:
        open_tickets = {int(position.ticket) for position in self._positions()}
        for ticket, stored in list(self.state.data["positions"].items()):
            if int(ticket) in open_tickets:
                continue
            pnl = self._closed_position_pnl(int(ticket), str(stored["opened_at"]))
            if pnl is not None:
                self.state.record_closed(stored, pnl, now)

    def _symbol_guard_reason(self, signal: LiveSignal, account: Any, now: datetime) -> Optional[str]:
        if signal.mode != "ICT" or signal.symbol not in SATELLITE_GUARDS:
            return None
        guard = SATELLITE_GUARDS[signal.symbol]
        day = self.state.data["day"]
        if int(day["symbol_losses"].get(signal.symbol, 0)) >= guard.stop_after_daily_losses:
            return "SYMBOL_DAILY_LOSS_COUNT_STOP"
        start_equity = float(day.get("start_equity", account.equity) or account.equity)
        pnl = float(day["symbol_pnl"].get(signal.symbol, 0.0))
        if pnl <= -(guard.daily_loss_cap_percent / 100.0) * start_equity:
            return "SYMBOL_DAILY_LOSS_CAP"
        cooldown = day["symbol_cooldown_until"].get(signal.symbol)
        if cooldown and now < datetime.fromisoformat(cooldown).astimezone(timezone.utc):
            return "SYMBOL_ROLLING_LOSS_COOLDOWN"
        same_symbol = [position for position in self._positions() if signal.symbol in "".join(ch for ch in str(position.symbol).upper() if ch.isalpha())]
        if len(same_symbol) >= guard.max_open_positions:
            return "SYMBOL_OPEN_POSITION_LIMIT"
        return None

    def place(self, signal: LiveSignal, now: Optional[datetime] = None) -> ExecutionResult:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        account = self.client.account_info()
        if account is None:
            return ExecutionResult(False, "ACCOUNT_UNAVAILABLE", "MT5 account information is unavailable")
        self.state.reset_day(now, float(account.equity))
        self.state.update_equity(float(account.equity))
        self.reconcile(now)

        if signal.key in self.state.data["seen"]:
            return ExecutionResult(False, "DUPLICATE_SIGNAL", "Signal was already processed")
        age = now - signal.signal_time.astimezone(timezone.utc)
        if age > timedelta(minutes=self.config.maximum_signal_age_minutes) or age < timedelta(minutes=-5):
            return ExecutionResult(False, "STALE_SIGNAL", f"Signal age is {age}")

        drawdown = self.state.drawdown_percent(float(account.equity))
        if drawdown >= self.config.live_hard_drawdown_percent:
            return ExecutionResult(False, "LIVE_DRAWDOWN_STOP", f"Live drawdown {drawdown:.2f}% reached the {self.config.live_hard_drawdown_percent:.2f}% limit")
        day_start = float(self.state.data["day"].get("start_equity", account.equity) or account.equity)
        daily_loss = max(0.0, day_start - float(account.equity)) / day_start * 100.0 if day_start > 0 else 0.0
        if daily_loss >= self.config.daily_account_loss_limit_percent:
            return ExecutionResult(False, "DAILY_ACCOUNT_LOSS_STOP", f"Daily loss {daily_loss:.2f}% reached the limit")

        guard_reason = self._symbol_guard_reason(signal, account, now)
        if guard_reason:
            return ExecutionResult(False, guard_reason, "Symbol loss or position guard rejected the signal")

        requested = min(float(signal.requested_risk_percent), self.config.max_live_risk_percent)
        governed = self.governor.apply(requested, drawdown)
        if governed <= 0:
            return ExecutionResult(False, "RESEARCH_DRAWDOWN_GOVERNOR_STOP", "Research drawdown governor returned zero risk")

        positions = self._positions()
        if len(positions) >= self.config.max_open_positions:
            return ExecutionResult(False, "MAX_OPEN_POSITIONS", "Maximum open positions reached")
        open_risk = sum(self._position_risk_dollars(position) for position in positions)
        balance = float(account.balance)
        intended_dollars = balance * governed / 100.0
        if open_risk + intended_dollars > balance * self.config.max_open_risk_percent / 100.0 + 1e-9:
            return ExecutionResult(False, "MAX_OPEN_RISK", "Combined broker-visible open risk would exceed the limit")

        info = self.client.symbol_info(signal.broker_symbol)
        tick = self.client.symbol_info_tick(signal.broker_symbol)
        if info is None or tick is None:
            return ExecutionResult(False, "MARKET_DATA_UNAVAILABLE", "Symbol information or tick is unavailable")
        pip = pip_size(info, signal.symbol)
        spread = (float(tick.ask) - float(tick.bid)) / pip
        if spread > self.config.spread_caps[signal.symbol]:
            return ExecutionResult(False, "SPREAD_TOO_WIDE", f"Spread {spread:.2f} pips exceeds {self.config.spread_caps[signal.symbol]:.2f}")

        is_buy = signal.side.upper() == "BUY"
        order_type = self.client.ORDER_TYPE_BUY if is_buy else self.client.ORDER_TYPE_SELL
        price = float(tick.ask if is_buy else tick.bid)
        stop = price - signal.stop_pips * pip if is_buy else price + signal.stop_pips * pip
        target = price + signal.target_pips * pip if is_buy else price - signal.target_pips * pip
        minimum_stop = float(getattr(info, "trade_stops_level", 0.0) or 0.0) * float(getattr(info, "point", 0.0) or 0.0)
        if abs(price - stop) + 1e-12 < minimum_stop or abs(target - price) + 1e-12 < minimum_stop:
            return ExecutionResult(False, "BROKER_STOP_DISTANCE", "SL or TP is inside the broker minimum stop distance")

        one_lot_loss = self.client.order_calc_profit(order_type, signal.broker_symbol, 1.0, price, stop)
        if one_lot_loss is None or abs(float(one_lot_loss)) <= 0:
            return ExecutionResult(False, "BROKER_SIZING_UNAVAILABLE", "order_calc_profit could not determine one-lot stop risk")
        volume = normalize_volume(info, intended_dollars / abs(float(one_lot_loss)))
        if volume <= 0:
            return ExecutionResult(False, "VOLUME_TOO_SMALL", "Risk-based volume is below the broker minimum")
        actual_risk_dollars = abs(float(self.client.order_calc_profit(order_type, signal.broker_symbol, volume, price, stop)))
        actual_risk_percent = actual_risk_dollars / balance * 100.0 if balance > 0 else 0.0
        if actual_risk_percent > governed + 1e-9:
            return ExecutionResult(False, "RISK_ROUNDING_EXCEEDED", "Normalized volume exceeded the approved risk")

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
            "comment": f"V143 {signal.mode} {signal.engine}"[:31],
            "type_time": self.client.ORDER_TIME_GTC,
            "type_filling": int(getattr(info, "filling_mode", getattr(self.client, "ORDER_FILLING_IOC", 1))),
        }
        proposal = {
            "signal": asdict(signal),
            "mode": self.config.execution_mode,
            "account_login": getattr(account, "login", None),
            "account_server": str(getattr(account, "server", "")),
            "is_demo": self._is_demo(account),
            "volume": volume,
            "risk_percent": actual_risk_percent,
            "spread_pips": spread,
            "drawdown_percent": drawdown,
            "request": request,
        }

        if self.config.execution_mode == "READ_ONLY":
            self.state.mark_seen(signal.key, now)
            return ExecutionResult(True, "READ_ONLY_PROPOSAL", "Validated proposal only; no broker order was sent", volume=volume, risk_percent=actual_risk_percent, proposal=proposal)
        if not self._is_demo(account):
            return ExecutionResult(False, "DEMO_ACCOUNT_REQUIRED", "Order transmission is restricted to MT5 demo accounts", proposal=proposal)
        if self.config.execution_mode == "APPROVAL" and not self.approval_callback(proposal):
            return ExecutionResult(False, "APPROVAL_DECLINED", "Exact YES approval was not received", proposal=proposal)
        if self.config.execution_mode == "AUTO" and not (self.config.allow_demo_auto and self.config.forward_gate_passed):
            return ExecutionResult(False, "AUTO_GATE_CLOSED", "AUTO requires both demo-auto and forward-validation gates", proposal=proposal)

        check = self.client.order_check(request)
        if check is None:
            return ExecutionResult(False, "ORDER_CHECK_NONE", f"order_check returned None: {self.client.last_error()}", proposal=proposal)
        check_retcode = getattr(check, "retcode", None)
        if check_retcode not in {0, getattr(self.client, "TRADE_RETCODE_DONE", None)}:
            return ExecutionResult(False, "ORDER_CHECK_REJECTED", f"order_check rejected request: {check_retcode} {getattr(check, 'comment', '')}", proposal=proposal)

        self.state.mark_seen(signal.key, now)
        result = self.client.order_send(request)
        if result is None:
            return ExecutionResult(False, "ORDER_SEND_NONE", f"order_send returned None: {self.client.last_error()}", proposal=proposal)
        if not _successful_retcode(self.client, getattr(result, "retcode", None)):
            return ExecutionResult(False, "ORDER_REJECTED", f"MT5 rejected order: {getattr(result, 'retcode', None)} {getattr(result, 'comment', '')}", proposal=proposal)
        ticket = int(getattr(result, "order", 0) or getattr(result, "deal", 0) or 0)
        if ticket <= 0:
            positions_after = self.client.positions_get(symbol=signal.broker_symbol) or []
            matching = [position for position in positions_after if int(getattr(position, "magic", 0) or 0) == request["magic"]]
            ticket = int(matching[-1].ticket) if matching else 0
        if ticket <= 0:
            return ExecutionResult(False, "TICKET_UNAVAILABLE", "MT5 accepted the request but no ticket was recoverable", volume=volume, risk_percent=actual_risk_percent, proposal=proposal)
        self.state.register_position(ticket, signal, actual_risk_dollars, now)
        return ExecutionResult(True, "ORDER_FILLED", "Demo order checked, sent and persisted", ticket=ticket, volume=volume, risk_percent=actual_risk_percent, proposal=proposal)
