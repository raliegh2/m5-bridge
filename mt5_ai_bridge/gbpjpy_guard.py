"""Persistent GBPJPY-specific risk and loss-cluster protection.

The guard is deliberately independent from signal generation. Any execution
adapter can ask it whether a new GBPJPY order is allowed and what maximum risk
percentage may be used. State is written atomically so restarting the bot does
not clear a same-day stop or an active cooldown.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class GBPJPYGuardConfig:
    normal_risk_cap_percent: float = 0.20
    post_loss_risk_cap_percent: float = 0.10
    max_open_positions: int = 1
    max_daily_losses: int = 2
    rolling_window_trades: int = 6
    rolling_net_r_stop: float = -2.0
    cooldown_hours: float = 4.0
    daily_net_r_stop: float = -2.0
    win_pressure_recovery: float = 0.50
    session_start_hour_utc: int = 7
    session_end_hour_utc: int = 20
    max_spread_pips: float = 3.0
    min_reward_risk: float = 1.50
    min_stop_pips: float = 15.0
    max_stop_pips: float = 150.0

    def validate(self) -> None:
        if self.normal_risk_cap_percent <= 0:
            raise ValueError("normal_risk_cap_percent must be positive")
        if not 0 < self.post_loss_risk_cap_percent <= self.normal_risk_cap_percent:
            raise ValueError("post-loss risk cap must be positive and no larger than normal risk")
        if self.max_open_positions != 1:
            raise ValueError("GBPJPY correction requires exactly one open position maximum")
        if self.max_daily_losses < 1:
            raise ValueError("max_daily_losses must be at least one")
        if self.rolling_window_trades < 2:
            raise ValueError("rolling_window_trades must be at least two")
        if self.rolling_net_r_stop >= 0 or self.daily_net_r_stop >= 0:
            raise ValueError("GBPJPY loss-stop thresholds must be negative R values")
        if self.cooldown_hours <= 0:
            raise ValueError("cooldown_hours must be positive")
        if not 0 < self.win_pressure_recovery < 1:
            raise ValueError("win_pressure_recovery must be between zero and one")
        if not 0 <= self.session_start_hour_utc <= 23:
            raise ValueError("session_start_hour_utc must be between 0 and 23")
        if not 1 <= self.session_end_hour_utc <= 24:
            raise ValueError("session_end_hour_utc must be between 1 and 24")
        if self.session_start_hour_utc >= self.session_end_hour_utc:
            raise ValueError("GBPJPY session must be a same-day UTC window")
        if self.max_spread_pips <= 0:
            raise ValueError("max_spread_pips must be positive")
        if self.min_reward_risk < 1.0:
            raise ValueError("min_reward_risk must be at least 1.0")
        if not 0 < self.min_stop_pips < self.max_stop_pips:
            raise ValueError("GBPJPY stop bounds are invalid")


@dataclass
class GBPJPYGuardState:
    day: Optional[str] = None
    daily_net_r: float = 0.0
    daily_losses: int = 0
    loss_pressure: float = 0.0
    block_rest_of_day: bool = False
    cooldown_until: Optional[str] = None
    recent_r: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class GBPJPYGuardDecision:
    ok: bool
    code: str
    message: str
    risk_cap_percent: float


class GBPJPYGuardStore:
    def __init__(self, path: str = "gbpjpy_guard_state.json",
                 config: GBPJPYGuardConfig = GBPJPYGuardConfig()) -> None:
        config.validate()
        self.path = Path(path)
        self.config = config
        self.state = self._load()

    def _load(self) -> GBPJPYGuardState:
        if not self.path.exists():
            return GBPJPYGuardState()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return GBPJPYGuardState(
            day=raw.get("day"),
            daily_net_r=float(raw.get("daily_net_r", 0.0)),
            daily_losses=int(raw.get("daily_losses", 0)),
            loss_pressure=float(raw.get("loss_pressure", 0.0)),
            block_rest_of_day=bool(raw.get("block_rest_of_day", False)),
            cooldown_until=raw.get("cooldown_until"),
            recent_r=[float(value) for value in raw.get("recent_r", [])],
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(self.state), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _utc(now: Optional[datetime]) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _roll_day(self, now: datetime) -> None:
        today = now.date().isoformat()
        if self.state.day == today:
            return
        self.state.day = today
        self.state.daily_net_r = 0.0
        self.state.daily_losses = 0
        self.state.loss_pressure = 0.0
        self.state.block_rest_of_day = False
        self.save()

    def _cooldown_until(self) -> Optional[datetime]:
        raw = self.state.cooldown_until
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            self.state.cooldown_until = None
            self.save()
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def decision(self, open_positions: int = 0,
                 now: Optional[datetime] = None) -> GBPJPYGuardDecision:
        now_utc = self._utc(now)
        self._roll_day(now_utc)

        if open_positions >= self.config.max_open_positions:
            return GBPJPYGuardDecision(
                False, "GBPJPY_ONE_POSITION_LIMIT",
                "A GBPJPY position is already open; stacking is disabled.", 0.0,
            )
        if self.state.block_rest_of_day:
            return GBPJPYGuardDecision(
                False, "GBPJPY_DAILY_STOP",
                "GBPJPY reached its daily loss stop and is disabled until the next UTC day.",
                0.0,
            )
        cooldown = self._cooldown_until()
        if cooldown is not None and now_utc < cooldown:
            return GBPJPYGuardDecision(
                False, "GBPJPY_COOLDOWN",
                f"GBPJPY is cooling down until {cooldown.isoformat()}.", 0.0,
            )
        if cooldown is not None and now_utc >= cooldown:
            self.state.cooldown_until = None
            self.save()

        reduced = self.state.loss_pressure > 0 or self.state.daily_net_r < 0
        cap = (self.config.post_loss_risk_cap_percent
               if reduced else self.config.normal_risk_cap_percent)
        return GBPJPYGuardDecision(
            True,
            "GBPJPY_REDUCED_RISK" if reduced else "GBPJPY_READY",
            "GBPJPY is allowed at reduced post-loss risk." if reduced
            else "GBPJPY is allowed at the normal guarded risk cap.",
            cap,
        )

    def in_session(self, now: Optional[datetime] = None) -> bool:
        hour = self._utc(now).hour
        return self.config.session_start_hour_utc <= hour < self.config.session_end_hour_utc

    def record_result(self, r_multiple: float,
                      now: Optional[datetime] = None) -> None:
        if not math.isfinite(r_multiple):
            raise ValueError("r_multiple must be finite")
        now_utc = self._utc(now)
        self._roll_day(now_utc)

        value = float(r_multiple)
        self.state.daily_net_r += value
        self.state.recent_r.append(value)
        keep = max(self.config.rolling_window_trades * 4, 24)
        self.state.recent_r = self.state.recent_r[-keep:]

        if value < 0:
            self.state.daily_losses += 1
            self.state.loss_pressure += 1.0
        elif value > 0:
            # A single small win does not erase a loss cluster.
            self.state.loss_pressure = max(
                0.0,
                self.state.loss_pressure - self.config.win_pressure_recovery,
            )

        recent = self.state.recent_r[-self.config.rolling_window_trades:]
        rolling_failed = (
            len(recent) >= 2 and sum(recent) <= self.config.rolling_net_r_stop
        )
        daily_failed = self.state.daily_net_r <= self.config.daily_net_r_stop
        count_failed = self.state.daily_losses >= self.config.max_daily_losses

        if rolling_failed:
            until = now_utc + timedelta(hours=self.config.cooldown_hours)
            current = self._cooldown_until()
            if current is None or until > current:
                self.state.cooldown_until = until.isoformat()
        if daily_failed or count_failed:
            self.state.block_rest_of_day = True
            until = now_utc + timedelta(hours=self.config.cooldown_hours)
            current = self._cooldown_until()
            if current is None or until > current:
                self.state.cooldown_until = until.isoformat()

        self.save()
