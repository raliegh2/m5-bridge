"""V14.21 demo-only automatic execution boundary.

This module extends the existing reconciled research-parity and V14.4
profit-guard executor. It does not create a second broker path. Every order
still passes the inherited spread, staleness, sizing, open-risk, drawdown,
reconciliation, ``order_check`` and demo-account controls before
``order_send`` can be reached.

V14.21 adds:

* exact expected demo login/server pinning;
* terminal and account trade-permission checks;
* explicit ``DEMO_AUTO`` acknowledgement and two independent AUTO gates;
* a filesystem kill switch;
* $250 daily and $500 total-equity loss stops;
* a two-consecutive-loss stop, reset on the next UTC trading day;
* live V14.20 range anti-consensus shadowing;
* a permanent ban on transmitting direct V14.19 range-reversion signals;
* a dedicated JSONL audit trail containing no credentials.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from dotenv import load_dotenv

from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_3_live_execution import ExecutionResult, LiveSignal
from .v14_3_research_parity_execution import (
    PARITY_MAX_COMBINED_OPEN_RISK_PERCENT,
    PARITY_MAX_TRADE_RISK_PERCENT,
    ResearchParityLiveRunnerConfig,
    ResearchParityState,
)
from .v14_4_profit_guard import ProfitGuardConfig
from .v14_4_profit_guard_execution import (
    ProfitGuardedLiveExecutor,
    ProfitGuardedState,
)
from .v14_20_range_anti_consensus_live import apply_live_range_anti_consensus

TRUTHY = {"1", "TRUE", "YES", "ON"}
REQUESTED_MODES = {"READ_ONLY", "APPROVAL", "DEMO_AUTO"}
AUTO_ACKNOWLEDGEMENT = "DEMO_ONLY"


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().upper() in TRUTHY


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def _optional_float(name: str) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else 0.0


@dataclass(frozen=True)
class V1421DemoAutoConfig(ResearchParityLiveRunnerConfig):
    """Pinned demo-AUTO configuration layered over research parity."""

    expected_login: int | None = None
    expected_server: str | None = None
    demo_acknowledgement: str = ""
    kill_switch_path: str = "state/V14_21_STOP"
    audit_log_path: str = "state/v14_21_demo_auto_executions.jsonl"
    daily_loss_limit_dollars: float = 250.0
    overall_loss_limit_dollars: float = 500.0
    maximum_consecutive_losses: int = 2
    maximum_tick_age_seconds: float = 10.0
    initial_balance_override: float = 0.0
    spread_caps: dict[str, float] = field(
        default_factory=lambda: {
            "GBPUSD": 2.0,
            "EURUSD": 1.5,
            "GBPJPY": 3.0,
            "AUDUSD": 1.8,
            "USDJPY": 2.0,
        }
    )

    @property
    def requested_mode(self) -> str:
        return "DEMO_AUTO" if self.execution_mode == "AUTO" else self.execution_mode

    @classmethod
    def from_env(cls) -> "V1421DemoAutoConfig":
        load_dotenv()
        requested_mode = os.getenv(
            "V14_21_EXECUTION_MODE",
            "READ_ONLY",
        ).strip().upper()
        if requested_mode not in REQUESTED_MODES:
            raise ValueError(
                "V14_21_EXECUTION_MODE must be READ_ONLY, APPROVAL or DEMO_AUTO"
            )
        execution_mode = "AUTO" if requested_mode == "DEMO_AUTO" else requested_mode
        config = cls(
            execution_mode=execution_mode,
            state_path=os.getenv(
                "V14_21_STATE_PATH",
                "state/v14_21_demo_auto_state.json",
            ),
            forward_gate_passed=_truthy("V14_21_FORWARD_GATE_PASSED"),
            allow_demo_auto=_truthy("V14_21_ALLOW_DEMO_AUTO"),
            max_deviation_points=int(
                os.getenv("V14_21_MAX_DEVIATION_POINTS", "10")
            ),
            maximum_signal_age_minutes=int(
                os.getenv("V14_21_MAX_SIGNAL_AGE_MINUTES", "90")
            ),
            expected_login=_optional_int("V14_21_EXPECTED_LOGIN"),
            expected_server=(
                os.getenv("V14_21_EXPECTED_SERVER", "").strip() or None
            ),
            demo_acknowledgement=os.getenv(
                "V14_21_ACKNOWLEDGE_DEMO_ONLY",
                "",
            ).strip(),
            kill_switch_path=os.getenv(
                "V14_21_KILL_SWITCH_PATH",
                "state/V14_21_STOP",
            ),
            audit_log_path=os.getenv(
                "V14_21_AUDIT_LOG_PATH",
                "state/v14_21_demo_auto_executions.jsonl",
            ),
            daily_loss_limit_dollars=float(
                os.getenv("V14_21_DAILY_LOSS_LIMIT_DOLLARS", "250")
            ),
            overall_loss_limit_dollars=float(
                os.getenv("V14_21_OVERALL_LOSS_LIMIT_DOLLARS", "500")
            ),
            maximum_consecutive_losses=int(
                os.getenv("V14_21_MAX_CONSECUTIVE_LOSSES", "2")
            ),
            maximum_tick_age_seconds=float(
                os.getenv("V14_21_MAX_TICK_AGE_SECONDS", "10")
            ),
            initial_balance_override=_optional_float(
                "V14_21_INITIAL_BALANCE"
            ),
            spread_caps={
                symbol: float(
                    os.getenv(f"V14_21_MAX_SPREAD_{symbol}", default)
                )
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
        super().validate()
        if self.daily_loss_limit_dollars <= 0:
            raise ValueError("V14.21 daily dollar loss limit must be positive")
        if self.overall_loss_limit_dollars <= 0:
            raise ValueError("V14.21 overall dollar loss limit must be positive")
        if self.daily_loss_limit_dollars > self.overall_loss_limit_dollars:
            raise ValueError("Daily loss limit cannot exceed overall loss limit")
        if self.maximum_consecutive_losses < 1:
            raise ValueError("Maximum consecutive losses must be positive")
        if self.maximum_tick_age_seconds <= 0:
            raise ValueError("Maximum tick age must be positive")
        if self.initial_balance_override < 0:
            raise ValueError("Initial balance override cannot be negative")
        if self.max_live_risk_percent != PARITY_MAX_TRADE_RISK_PERCENT:
            raise ValueError("V14.21 must retain the 0.80% trade-risk ceiling")
        if self.max_open_risk_percent != PARITY_MAX_COMBINED_OPEN_RISK_PERCENT:
            raise ValueError("V14.21 must retain the 3.25% combined-risk cap")
        if self.execution_mode == "AUTO":
            if not self.forward_gate_passed:
                raise ValueError(
                    "DEMO_AUTO requires V14_21_FORWARD_GATE_PASSED=true"
                )
            if not self.allow_demo_auto:
                raise ValueError(
                    "DEMO_AUTO requires V14_21_ALLOW_DEMO_AUTO=true"
                )
            if self.demo_acknowledgement != AUTO_ACKNOWLEDGEMENT:
                raise ValueError(
                    "DEMO_AUTO requires "
                    "V14_21_ACKNOWLEDGE_DEMO_ONLY=DEMO_ONLY"
                )
            if not self.expected_login:
                raise ValueError(
                    "DEMO_AUTO requires V14_21_EXPECTED_LOGIN"
                )
            if not self.expected_server:
                raise ValueError(
                    "DEMO_AUTO requires V14_21_EXPECTED_SERVER"
                )


class V1421DemoAutoState(ProfitGuardedState):
    """Profit-guard state plus account baseline and all-mode loss sequence."""

    @staticmethod
    def _new_day(date: str | None, equity: float) -> dict[str, Any]:
        payload = ResearchParityState._new_day(date, equity)
        payload["v14_21_consecutive_losses"] = 0
        return payload

    def _default(self) -> dict[str, Any]:
        payload = super()._default()
        payload.setdefault("v14_21_initial_balance", 0.0)
        payload.setdefault("v14_21_initialised_at", None)
        payload.setdefault("v14_21_closed_trades", 0)
        return payload

    def ensure_initial_balance(
        self,
        account: Any,
        now: datetime,
        override: float = 0.0,
    ) -> None:
        current = float(
            self.data.get("v14_21_initial_balance", 0.0) or 0.0
        )
        if current > 0:
            return
        balance = float(override or getattr(account, "balance", 0.0) or 0.0)
        if balance <= 0:
            raise ValueError("A positive demo account baseline is required")
        self.data["v14_21_initial_balance"] = balance
        self.data["v14_21_initialised_at"] = now.isoformat()
        self.save()

    def record_closed(
        self,
        position: dict[str, Any],
        pnl: float,
        closed_at: datetime,
    ) -> None:
        super().record_closed(position, pnl, closed_at)
        day = self.data.setdefault(
            "day",
            self._new_day(
                closed_at.astimezone(timezone.utc).date().isoformat(),
                0.0,
            ),
        )
        losses = int(day.get("v14_21_consecutive_losses", 0) or 0)
        if float(pnl) < 0:
            losses += 1
        elif float(pnl) > 0:
            losses = 0
        day["v14_21_consecutive_losses"] = losses
        self.data["v14_21_closed_trades"] = int(
            self.data.get("v14_21_closed_trades", 0) or 0
        ) + 1
        self.save()


@dataclass(frozen=True)
class RuntimeGuardResult:
    allowed: bool
    code: str
    message: str


def validate_demo_runtime(
    client: Any,
    config: V1421DemoAutoConfig,
    account: Any | None = None,
    terminal: Any | None = None,
) -> RuntimeGuardResult:
    """Fail closed unless the connected terminal is the pinned demo account."""

    account = account if account is not None else client.account_info()
    terminal = (
        terminal
        if terminal is not None
        else (client.terminal_info() if hasattr(client, "terminal_info") else None)
    )
    if terminal is None:
        return RuntimeGuardResult(
            False,
            "TERMINAL_INFO_UNAVAILABLE",
            "MT5 terminal information is unavailable",
        )
    if not bool(getattr(terminal, "connected", False)):
        return RuntimeGuardResult(
            False,
            "TERMINAL_DISCONNECTED",
            "MT5 terminal is not connected",
        )
    if account is None:
        return RuntimeGuardResult(
            False,
            "ACCOUNT_UNAVAILABLE",
            "MT5 account information is unavailable",
        )
    demo_constant = int(getattr(client, "ACCOUNT_TRADE_MODE_DEMO", 0))
    trade_mode = int(getattr(account, "trade_mode", -1))
    if trade_mode != demo_constant:
        return RuntimeGuardResult(
            False,
            "DEMO_ACCOUNT_REQUIRED",
            "V14.21 may run only on a confirmed MT5 demo account",
        )

    transmitting = config.execution_mode in {"APPROVAL", "AUTO"}
    if transmitting:
        if not bool(getattr(terminal, "trade_allowed", False)):
            return RuntimeGuardResult(
                False,
                "TERMINAL_TRADING_DISABLED",
                "Algorithmic trading is disabled in the MT5 terminal",
            )
        if bool(getattr(terminal, "tradeapi_disabled", False)):
            return RuntimeGuardResult(
                False,
                "TERMINAL_TRADE_API_DISABLED",
                "The MT5 terminal has disabled external trading APIs",
            )
        if not bool(getattr(account, "trade_allowed", False)):
            return RuntimeGuardResult(
                False,
                "ACCOUNT_TRADING_DISABLED",
                "Trading is disabled for the connected demo account",
            )
        if not bool(getattr(account, "trade_expert", False)):
            return RuntimeGuardResult(
                False,
                "ACCOUNT_EXPERT_TRADING_DISABLED",
                "Expert/API trading is disabled for the demo account",
            )

    login = int(getattr(account, "login", 0) or 0)
    server = str(getattr(account, "server", "") or "").strip()
    if config.execution_mode == "AUTO" or config.expected_login is not None:
        if login != int(config.expected_login or 0):
            return RuntimeGuardResult(
                False,
                "EXPECTED_LOGIN_MISMATCH",
                "Connected demo login does not match V14_21_EXPECTED_LOGIN",
            )
    if config.execution_mode == "AUTO" or config.expected_server:
        if server.casefold() != str(config.expected_server or "").strip().casefold():
            return RuntimeGuardResult(
                False,
                "EXPECTED_SERVER_MISMATCH",
                "Connected demo server does not match V14_21_EXPECTED_SERVER",
            )
    return RuntimeGuardResult(
        True,
        "DEMO_RUNTIME_CONFIRMED",
        "Pinned MT5 demo account and terminal permissions confirmed",
    )


class V1421DemoAutoExecutor(ProfitGuardedLiveExecutor):
    """V14.20-aware demo executor with explicit automatic-transmission gates."""

    def __init__(
        self,
        client: Any,
        config: V1421DemoAutoConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
        guard_config: Optional[ProfitGuardConfig] = None,
    ) -> None:
        super().__init__(client, config, approval_callback, guard_config)
        self.config = config
        self.state = V1421DemoAutoState(config.state_path)

    def _append_audit(
        self,
        *,
        now: datetime,
        signal: LiveSignal,
        result: ExecutionResult,
        account: Any | None,
        terminal: Any | None,
    ) -> None:
        path = Path(self.config.audit_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "created_at": now.isoformat(),
            "runner": "V14.21_DEMO_AUTO",
            "requested_mode": self.config.requested_mode,
            "signal_key": signal.key,
            "signal": asdict(signal),
            "result": asdict(result),
            "account": {
                "login": getattr(account, "login", None),
                "server": str(getattr(account, "server", "") or ""),
                "trade_mode": getattr(account, "trade_mode", None),
            },
            "terminal": {
                "connected": getattr(terminal, "connected", None),
                "trade_allowed": getattr(terminal, "trade_allowed", None),
                "tradeapi_disabled": getattr(
                    terminal,
                    "tradeapi_disabled",
                    None,
                ),
            },
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
            handle.flush()

    def _finish(
        self,
        signal: LiveSignal,
        result: ExecutionResult,
        now: datetime,
        account: Any | None,
        terminal: Any | None,
    ) -> ExecutionResult:
        self._append_audit(
            now=now,
            signal=signal,
            result=result,
            account=account,
            terminal=terminal,
        )
        return result

    def _loss_stop_reason(self, account: Any) -> tuple[str, str] | None:
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        day = self.state.data.get("day", {})
        day_start = float(day.get("start_equity", equity) or equity)
        daily_loss = max(0.0, day_start - equity)
        if daily_loss >= self.config.daily_loss_limit_dollars:
            return (
                "V14_21_DAILY_DOLLAR_STOP",
                f"Demo equity is down ${daily_loss:.2f} from UTC day start; "
                f"limit is ${self.config.daily_loss_limit_dollars:.2f}",
            )
        initial = float(
            self.state.data.get("v14_21_initial_balance", 0.0) or 0.0
        )
        total_loss = max(0.0, initial - equity)
        if initial > 0 and total_loss >= self.config.overall_loss_limit_dollars:
            return (
                "V14_21_OVERALL_DOLLAR_STOP",
                f"Demo equity is down ${total_loss:.2f} from the V14.21 baseline; "
                f"limit is ${self.config.overall_loss_limit_dollars:.2f}",
            )
        consecutive = int(
            day.get("v14_21_consecutive_losses", 0) or 0
        )
        if consecutive >= self.config.maximum_consecutive_losses:
            return (
                "V14_21_CONSECUTIVE_LOSS_STOP",
                f"{consecutive} consecutive closed losses reached the "
                f"{self.config.maximum_consecutive_losses}-loss UTC-day stop",
            )
        return None

    def _tick_guard_reason(
        self,
        signal: LiveSignal,
        now: datetime,
    ) -> tuple[str, str] | None:
        tick = self.client.symbol_info_tick(signal.broker_symbol)
        if tick is None:
            return (
                "TICK_UNAVAILABLE",
                "Current broker tick is unavailable",
            )
        milliseconds = int(getattr(tick, "time_msc", 0) or 0)
        seconds = int(getattr(tick, "time", 0) or 0)
        tick_time = (
            milliseconds / 1000.0
            if milliseconds > 0
            else float(seconds)
        )
        if tick_time <= 0:
            return (
                "TICK_TIMESTAMP_UNAVAILABLE",
                "Current broker tick has no usable timestamp",
            )
        age = now.timestamp() - tick_time
        if age > self.config.maximum_tick_age_seconds:
            return (
                "STALE_TICK",
                f"Broker tick is {age:.2f}s old; "
                f"limit is {self.config.maximum_tick_age_seconds:.2f}s",
            )
        if age < -5.0:
            return (
                "FUTURE_TICK",
                f"Broker tick is {-age:.2f}s ahead of runner time",
            )
        return None

    @staticmethod
    def _is_direct_range_signal(signal: LiveSignal) -> bool:
        engine = str(signal.engine).upper()
        family = str(signal.metadata.get("family", "")).upper()
        source_mode = str(signal.metadata.get("source_mode", "")).upper()
        return (
            "RANGE_REVERSION" in engine
            or "D1_RANGE_MEAN_REVERSION" in family
            or source_mode == "RANGE_SHADOW"
        )

    def _v14_20_filter_reason(
        self,
        signal: LiveSignal,
    ) -> str | None:
        payload = signal.metadata.get("v14_20_range_anti_consensus")
        current = CostRegimeDecision(
            funded=True,
            regime="LIVE_CANDIDATE",
            risk_percent=min(
                float(signal.requested_risk_percent),
                PARITY_MAX_TRADE_RISK_PERCENT,
            ),
            reason="V14.21 live candidate",
            all_in_cost_r=float(
                signal.metadata.get("all_in_cost_r", 0.0) or 0.0
            ),
            target_r=(
                float(signal.target_pips) / float(signal.stop_pips)
                if signal.stop_pips > 0
                else None
            ),
        )
        filtered = apply_live_range_anti_consensus(
            current,
            payload if isinstance(payload, Mapping) else None,
        )
        return filtered.reason if filtered.is_shadow else None

    def runtime_snapshot(self) -> dict[str, Any]:
        account = self.client.account_info()
        terminal = (
            self.client.terminal_info()
            if hasattr(self.client, "terminal_info")
            else None
        )
        guard = validate_demo_runtime(
            self.client,
            self.config,
            account,
            terminal,
        )
        return {
            "allowed": guard.allowed,
            "code": guard.code,
            "message": guard.message,
            "requested_mode": self.config.requested_mode,
            "expected_login": self.config.expected_login,
            "expected_server": self.config.expected_server,
            "kill_switch_path": self.config.kill_switch_path,
            "kill_switch_active": Path(self.config.kill_switch_path).exists(),
            "daily_loss_limit_dollars": self.config.daily_loss_limit_dollars,
            "overall_loss_limit_dollars": self.config.overall_loss_limit_dollars,
            "maximum_consecutive_losses": self.config.maximum_consecutive_losses,
            "account_login": getattr(account, "login", None),
            "account_server": str(getattr(account, "server", "") or ""),
            "demo_confirmed": (
                int(getattr(account, "trade_mode", -1))
                == int(getattr(self.client, "ACCOUNT_TRADE_MODE_DEMO", 0))
                if account is not None
                else False
            ),
        }

    def place(
        self,
        signal: LiveSignal,
        now: Optional[datetime] = None,
    ) -> ExecutionResult:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        account = self.client.account_info()
        terminal = (
            self.client.terminal_info()
            if hasattr(self.client, "terminal_info")
            else None
        )

        if Path(self.config.kill_switch_path).exists():
            return self._finish(
                signal,
                ExecutionResult(
                    False,
                    "V14_21_KILL_SWITCH",
                    f"Kill-switch file exists: {self.config.kill_switch_path}",
                ),
                now,
                account,
                terminal,
            )

        runtime = validate_demo_runtime(
            self.client,
            self.config,
            account,
            terminal,
        )
        if not runtime.allowed:
            return self._finish(
                signal,
                ExecutionResult(False, runtime.code, runtime.message),
                now,
                account,
                terminal,
            )
        assert account is not None

        self.state.reset_day(now, float(getattr(account, "equity", 0.0) or 0.0))
        self.state.ensure_initial_balance(
            account,
            now,
            self.config.initial_balance_override,
        )
        self.reconcile(now)

        stop = self._loss_stop_reason(account)
        if stop is not None:
            code, message = stop
            return self._finish(
                signal,
                ExecutionResult(False, code, message),
                now,
                account,
                terminal,
            )

        if self._is_direct_range_signal(signal):
            return self._finish(
                signal,
                ExecutionResult(
                    False,
                    "V14_19_RANGE_SHADOW_ONLY",
                    "Direct range mean-reversion orders remain permanently "
                    "shadow-only in V14.21",
                ),
                now,
                account,
                terminal,
            )

        filtered_reason = self._v14_20_filter_reason(signal)
        if filtered_reason is not None:
            return self._finish(
                signal,
                ExecutionResult(
                    False,
                    "V14_20_RANGE_CONFLICT_SHADOW",
                    filtered_reason,
                    risk_percent=0.0,
                ),
                now,
                account,
                terminal,
            )

        tick_stop = self._tick_guard_reason(signal, now)
        if tick_stop is not None:
            code, message = tick_stop
            return self._finish(
                signal,
                ExecutionResult(False, code, message),
                now,
                account,
                terminal,
            )

        result = super().place(signal, now=now)
        return self._finish(
            signal,
            result,
            now,
            account,
            terminal,
        )


__all__ = [
    "AUTO_ACKNOWLEDGEMENT",
    "REQUESTED_MODES",
    "RuntimeGuardResult",
    "V1421DemoAutoConfig",
    "V1421DemoAutoExecutor",
    "V1421DemoAutoState",
    "validate_demo_runtime",
]
