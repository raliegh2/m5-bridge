"""Account-level session circuit breakers for the live MT5 bridge.

The strategy engines remain unchanged. This module wraps the MT5 client and
centrally gates every NEW entry request while allowing position closes and
stop-loss modifications to continue.

State is persisted to JSON so a terminal restart cannot clear a daily lock,
profit high-water mark, consecutive-loss count, or cooldown.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

from .books import build_books
from .execution import MAGIC as EXECUTION_MAGIC
from .logging_config import get_logger
from .trade_manager import MAGIC as CLOSE_MAGIC

log = get_logger("session_guard")

_EPS = 1e-9
_BLOCK_RETCODE = -10050


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _default_state_path(settings) -> str:
    explicit = os.getenv("SESSION_STATE_PATH", "").strip()
    if explicit:
        return explicit
    db_path = str(getattr(settings, "db_path", "") or "")
    if not db_path or db_path == ":memory:":
        return "session_guard_state.json"
    stem = db_path[:-3] if db_path.lower().endswith(".db") else db_path
    return f"{stem}_session_guard.json"


@dataclass(frozen=True)
class SessionGuardConfig:
    enabled: bool = True

    enable_daily_loss_limit: bool = True
    max_daily_loss_percent: float = 1.0
    close_positions_on_daily_stop: bool = False

    enable_profit_giveback_stop: bool = True
    profit_lock_activation_percent: float = 1.0
    max_profit_giveback_percent: float = 40.0
    close_positions_on_profit_stop: bool = False

    enable_consecutive_loss_stop: bool = True
    max_consecutive_losses: int = 3
    loss_cooldown_minutes: int = 60
    stop_for_day_after_loss_limit: bool = True

    enable_trade_limit: bool = True
    max_trades_per_day: int = 8
    max_trades_per_symbol_per_day: int = 4
    minimum_minutes_between_entries: int = 15

    minimum_lot: float = 0.01
    maximum_lot: float = 0.40

    cancel_pending_on_stop: bool = True
    history_sync_seconds: float = 2.0
    history_lookback_days: int = 30
    state_path: str = "session_guard_state.json"

    @classmethod
    def from_settings(cls, settings) -> "SessionGuardConfig":
        existing_daily_cap = int(getattr(settings, "max_trades_per_day", 8) or 8)
        configured_daily_cap = _env_int("SESSION_MAX_TRADES_PER_DAY", 8)
        # The guard may be tighter than the strategy-level cap, never looser.
        max_daily = min(existing_daily_cap, configured_daily_cap) \
            if existing_daily_cap > 0 and configured_daily_cap > 0 \
            else max(existing_daily_cap, configured_daily_cap)
        return cls(
            enabled=_env_bool("SESSION_GUARD", True),
            enable_daily_loss_limit=_env_bool(
                "SESSION_ENABLE_DAILY_LOSS_LIMIT", True),
            max_daily_loss_percent=_env_float(
                "SESSION_MAX_DAILY_LOSS_PERCENT", 1.0),
            close_positions_on_daily_stop=_env_bool(
                "SESSION_CLOSE_POSITIONS_ON_DAILY_STOP", False),
            enable_profit_giveback_stop=_env_bool(
                "SESSION_ENABLE_PROFIT_GIVEBACK_STOP", True),
            profit_lock_activation_percent=_env_float(
                "SESSION_PROFIT_LOCK_ACTIVATION_PERCENT", 1.0),
            max_profit_giveback_percent=_env_float(
                "SESSION_MAX_PROFIT_GIVEBACK_PERCENT", 40.0),
            close_positions_on_profit_stop=_env_bool(
                "SESSION_CLOSE_POSITIONS_ON_PROFIT_STOP", False),
            enable_consecutive_loss_stop=_env_bool(
                "SESSION_ENABLE_CONSECUTIVE_LOSS_STOP", True),
            max_consecutive_losses=max(
                1, _env_int("SESSION_MAX_CONSECUTIVE_LOSSES", 3)),
            loss_cooldown_minutes=max(
                0, _env_int("SESSION_LOSS_COOLDOWN_MINUTES", 60)),
            stop_for_day_after_loss_limit=_env_bool(
                "SESSION_STOP_FOR_DAY_AFTER_LOSS_LIMIT", True),
            enable_trade_limit=_env_bool("SESSION_ENABLE_TRADE_LIMIT", True),
            max_trades_per_day=max(0, max_daily),
            max_trades_per_symbol_per_day=max(
                0, _env_int("SESSION_MAX_TRADES_PER_SYMBOL_PER_DAY", 4)),
            minimum_minutes_between_entries=max(
                0, _env_int("SESSION_MINIMUM_MINUTES_BETWEEN_ENTRIES", 15)),
            minimum_lot=max(0.0, _env_float("SESSION_MINIMUM_LOT", 0.01)),
            maximum_lot=max(0.0, _env_float("SESSION_MAXIMUM_LOT", 0.40)),
            cancel_pending_on_stop=_env_bool(
                "SESSION_CANCEL_PENDING_ON_STOP", True),
            history_sync_seconds=max(
                0.0, _env_float("SESSION_HISTORY_SYNC_SECONDS", 2.0)),
            history_lookback_days=max(
                1, _env_int("SESSION_HISTORY_LOOKBACK_DAYS", 30)),
            state_path=_default_state_path(settings),
        )


class RiskGuardedClient:
    """Transparent MT5 client wrapper with account-level entry circuit breakers."""

    def __init__(self, client, settings, journal=None,
                 config: Optional[SessionGuardConfig] = None) -> None:
        self._client = client
        self.settings = settings
        self.journal = journal
        self.config = config or SessionGuardConfig.from_settings(settings)
        self._lock = threading.RLock()
        self._last_history_sync = 0.0
        self._last_persist = 0.0
        self._last_reject_reason = ""
        self._last_reject_at = 0.0
        self._managed_magics = self._build_managed_magics()
        self._state = self._load_state()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    # ------------------------------------------------------------------
    # Public client surface
    # ------------------------------------------------------------------
    def account_info(self):
        account = self._client.account_info()
        if account is not None and self.config.enabled:
            try:
                self._refresh(account)
            except Exception as exc:  # noqa: BLE001
                log.warning("Session guard refresh failed: %s", exc)
        return account

    def order_send(self, request: dict):
        if not self.config.enabled or not self._is_new_entry(request):
            return self._client.order_send(request)

        request = dict(request)
        symbol = str(request.get("symbol") or "")
        volume = float(request.get("volume") or 0.0)
        allowed, reason = self.can_open_new_trade(
            symbol, request.get("type"), volume)

        if not allowed:
            self._log_rejection(reason)
            return SimpleNamespace(
                retcode=_BLOCK_RETCODE,
                comment=f"Session guard: {reason}",
                order=0,
                deal=0,
            )

        result = self._client.order_send(request)
        done = getattr(result, "retcode", None) == self._const(
            "TRADE_RETCODE_DONE", getattr(self._client, "TRADE_RETCODE_DONE", None))
        if result is not None and done:
            self._record_local_entry(symbol)
        return result

    def can_open_new_trade(self, symbol: str, order_type,
                           requested_volume: float) -> tuple[bool, str]:
        """Central permission check used by every live entry order."""
        del order_type  # Reserved for future side-specific controls.
        with self._lock:
            account = self._client.account_info()
            if account is None:
                return False, "account information unavailable"
            self._refresh(account)

            now = self._broker_now()
            now_ts = now.timestamp()
            state = self._state
            cfg = self.config

            if state.get("daily_lock"):
                return False, state.get("lock_reason") or "daily trading lock active"

            cooldown_until = float(state.get("cooldown_until") or 0.0)
            if cooldown_until > now_ts:
                remaining = max(1, int((cooldown_until - now_ts + 59) // 60))
                return False, (
                    f"loss cooldown active for approximately {remaining} more minute(s)"
                )
            if cooldown_until and cooldown_until <= now_ts:
                state["cooldown_until"] = 0.0
                state["consecutive_losses"] = 0
                self._save_state(force=True)

            if requested_volume <= 0:
                return False, "requested volume is not positive"
            if cfg.minimum_lot > 0 and requested_volume + _EPS < cfg.minimum_lot:
                return False, (
                    f"requested volume {requested_volume:g} is below "
                    f"SESSION_MINIMUM_LOT {cfg.minimum_lot:g}"
                )
            if cfg.maximum_lot > 0 and requested_volume > cfg.maximum_lot + _EPS:
                return False, (
                    f"requested volume {requested_volume:g} exceeds "
                    f"SESSION_MAXIMUM_LOT {cfg.maximum_lot:g}"
                )

            if cfg.enable_trade_limit:
                total = int(state.get("trades_today") or 0)
                if cfg.max_trades_per_day > 0 and total >= cfg.max_trades_per_day:
                    return False, (
                        f"daily trade limit reached "
                        f"({total}/{cfg.max_trades_per_day})"
                    )
                per_symbol = dict(state.get("trades_by_symbol") or {})
                sym_count = int(per_symbol.get(symbol.upper(), 0))
                if (cfg.max_trades_per_symbol_per_day > 0
                        and sym_count >= cfg.max_trades_per_symbol_per_day):
                    return False, (
                        f"{symbol} daily trade limit reached "
                        f"({sym_count}/{cfg.max_trades_per_symbol_per_day})"
                    )
                last_entry = float(state.get("last_entry_time") or 0.0)
                wait_seconds = cfg.minimum_minutes_between_entries * 60
                if wait_seconds > 0 and last_entry > 0 \
                        and now_ts - last_entry < wait_seconds:
                    remaining = max(
                        1, int((wait_seconds - (now_ts - last_entry) + 59) // 60))
                    return False, (
                        f"minimum entry interval active for approximately "
                        f"{remaining} more minute(s)"
                    )

            return True, "session risk checks passed"

    def status(self) -> dict:
        """Return a copy of the latest persisted guard status."""
        with self._lock:
            return dict(self._state)

    # ------------------------------------------------------------------
    # State and risk calculations
    # ------------------------------------------------------------------
    def _refresh(self, account) -> None:
        with self._lock:
            now = self._broker_now()
            day = now.date().isoformat()
            balance = float(getattr(account, "balance", 0.0) or 0.0)
            equity = float(getattr(account, "equity", balance) or balance)
            positions = self._client.positions_get() or []

            if self._state.get("day") != day:
                self._reset_day(day, balance, equity, account, positions)

            self._sync_history(now, positions, account)

            state = self._state
            state["balance"] = round(balance, 2)
            state["equity"] = round(equity, 2)
            start_balance = float(state.get("day_start_balance") or balance or 1.0)
            daily_equity_pl = equity - start_balance
            state["daily_equity_pl"] = round(daily_equity_pl, 2)

            peak_equity = max(float(state.get("peak_equity") or equity), equity)
            state["peak_equity"] = round(peak_equity, 2)
            peak_profit = max(float(state.get("peak_profit") or 0.0),
                              peak_equity - start_balance)
            state["peak_profit"] = round(peak_profit, 2)
            giveback = max(0.0, peak_profit - daily_equity_pl)
            state["profit_giveback"] = round(giveback, 2)

            cfg = self.config
            if not state.get("daily_lock") and cfg.enable_daily_loss_limit \
                    and cfg.max_daily_loss_percent > 0 and start_balance > 0:
                limit_amount = start_balance * cfg.max_daily_loss_percent / 100.0
                if daily_equity_pl <= -limit_amount + _EPS:
                    reason = (
                        f"daily equity loss limit reached: P/L "
                        f"{daily_equity_pl:+.2f}, limit -{limit_amount:.2f} "
                        f"({cfg.max_daily_loss_percent:g}% of "
                        f"{start_balance:.2f})"
                    )
                    self._lock_day(
                        "daily_loss", reason, account, positions,
                        close_positions=cfg.close_positions_on_daily_stop)

            if not state.get("daily_lock") and cfg.enable_profit_giveback_stop \
                    and cfg.profit_lock_activation_percent > 0 \
                    and cfg.max_profit_giveback_percent >= 0 and start_balance > 0:
                activation = start_balance * cfg.profit_lock_activation_percent / 100.0
                allowed_giveback = peak_profit * cfg.max_profit_giveback_percent / 100.0
                if peak_profit >= activation - _EPS \
                        and giveback >= allowed_giveback - _EPS \
                        and peak_profit > 0:
                    floor = peak_profit - allowed_giveback
                    reason = (
                        f"profit giveback stop reached: peak +{peak_profit:.2f}, "
                        f"current {daily_equity_pl:+.2f}, giveback {giveback:.2f} "
                        f"({cfg.max_profit_giveback_percent:g}%); "
                        f"profit floor +{floor:.2f}"
                    )
                    self._lock_day(
                        "profit_giveback", reason, account, positions,
                        close_positions=cfg.close_positions_on_profit_stop)

            self._save_state()

    def _reset_day(self, day: str, balance: float, equity: float,
                   account=None, positions=None) -> None:
        self._state = {
            "version": 1,
            "day": day,
            "day_start_balance": round(balance, 2),
            "day_start_equity": round(equity, 2),
            "balance": round(balance, 2),
            "equity": round(equity, 2),
            "daily_realized_pl": 0.0,
            "daily_equity_pl": round(equity - balance, 2),
            "peak_equity": round(equity, 2),
            "peak_profit": max(0.0, round(equity - balance, 2)),
            "profit_giveback": 0.0,
            "trades_today": 0,
            "trades_by_symbol": {},
            "last_entry_time": 0.0,
            "consecutive_losses": 0,
            "last_loss_time": 0.0,
            "cooldown_until": 0.0,
            "daily_lock": False,
            "lock_kind": "",
            "lock_reason": "",
            "locked_at": 0.0,
            "protective_action_done": False,
            "processed_position_ids": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._last_history_sync = 0.0
        self._save_state(force=True)
        self._emit(
            "info",
            f"Session guard reset for broker day {day}; "
            f"start balance {balance:.2f}, start equity {equity:.2f}.",
            account,
            positions or [],
        )

    def _sync_history(self, now: datetime, positions, account) -> None:
        cfg = self.config
        now_ts = now.timestamp()
        if cfg.history_sync_seconds > 0 \
                and now_ts - self._last_history_sync < cfg.history_sync_seconds:
            return
        self._last_history_sync = now_ts

        fn = getattr(self._client, "history_deals_get", None)
        if fn is None:
            fn = getattr(getattr(self._client, "_mt5", None),
                         "history_deals_get", None)
        if fn is None:
            return

        day_start = datetime.combine(now.date(), datetime.min.time(),
                                     tzinfo=timezone.utc)
        lookback = day_start - timedelta(days=cfg.history_lookback_days)
        try:
            deals = fn(lookback, now + timedelta(minutes=1)) or []
        except Exception as exc:  # noqa: BLE001
            log.debug("Session guard could not read deal history: %s", exc)
            return

        entry_in = self._const("DEAL_ENTRY_IN", 0)
        exit_entries = {
            self._const("DEAL_ENTRY_OUT", 1),
            self._const("DEAL_ENTRY_INOUT", 2),
            self._const("DEAL_ENTRY_OUT_BY", 3),
        }
        open_ids = {
            int(getattr(p, "identifier",
                        getattr(p, "ticket", 0)) or 0)
            for p in positions
        }

        groups: dict[int, dict[str, Any]] = {}
        entry_keys: set[tuple[Any, ...]] = set()
        entries_by_symbol: dict[str, int] = {}
        latest_entry = 0.0

        for deal in sorted(deals, key=self._deal_sort_key):
            pid = int(getattr(deal, "position_id", 0) or 0)
            if pid <= 0:
                continue
            magic = int(getattr(deal, "magic", 0) or 0)
            ts = self._deal_timestamp(deal)
            entry = getattr(deal, "entry", None)
            symbol = str(getattr(deal, "symbol", "") or "").upper()
            group = groups.setdefault(pid, {
                "managed": False,
                "net": 0.0,
                "has_exit": False,
                "exit_time": 0.0,
                "symbol": symbol,
            })
            group["managed"] = bool(
                group["managed"] or magic in self._managed_magics)
            group["symbol"] = group["symbol"] or symbol
            group["net"] += (
                float(getattr(deal, "profit", 0.0) or 0.0)
                + float(getattr(deal, "commission", 0.0) or 0.0)
                + float(getattr(deal, "swap", 0.0) or 0.0)
                + float(getattr(deal, "fee", 0.0) or 0.0)
            )
            if entry in exit_entries:
                group["has_exit"] = True
                group["exit_time"] = max(float(group["exit_time"]), ts)

            if magic in self._managed_magics and entry == entry_in \
                    and ts >= day_start.timestamp():
                key = (
                    getattr(deal, "order", None)
                    or getattr(deal, "ticket", None)
                    or (pid, ts, symbol)
                )
                unique_key = (key, symbol)
                if unique_key not in entry_keys:
                    entry_keys.add(unique_key)
                    entries_by_symbol[symbol] = entries_by_symbol.get(symbol, 0) + 1
                latest_entry = max(latest_entry, ts)

        closed = []
        realized = 0.0
        for pid, group in groups.items():
            if not group["managed"] or not group["has_exit"]:
                continue
            if pid in open_ids:
                continue  # Partial close: wait until the position is fully closed.
            if float(group["exit_time"]) < day_start.timestamp():
                continue
            realized += float(group["net"])
            closed.append((float(group["exit_time"]), pid, float(group["net"]),
                           str(group["symbol"])))

        state = self._state
        state["trades_today"] = max(
            int(state.get("trades_today") or 0), len(entry_keys))
        current_by_symbol = dict(state.get("trades_by_symbol") or {})
        for symbol, count in entries_by_symbol.items():
            current_by_symbol[symbol] = max(
                int(current_by_symbol.get(symbol, 0)), int(count))
        state["trades_by_symbol"] = current_by_symbol
        state["last_entry_time"] = max(
            float(state.get("last_entry_time") or 0.0), latest_entry)
        state["daily_realized_pl"] = round(realized, 2)

        processed = {
            int(v) for v in state.get("processed_position_ids", [])
            if str(v).lstrip("-").isdigit()
        }
        changed = False
        for exit_time, pid, net, symbol in sorted(closed):
            if pid in processed:
                continue
            processed.add(pid)
            changed = True
            if net < -0.005:
                state["consecutive_losses"] = int(
                    state.get("consecutive_losses") or 0) + 1
                state["last_loss_time"] = exit_time
                count = int(state["consecutive_losses"])
                self._emit(
                    "warning",
                    f"Completed loss detected on {symbol or 'managed position'} "
                    f"(position {pid}): net {net:+.2f}; "
                    f"consecutive losses {count}/"
                    f"{cfg.max_consecutive_losses}.",
                    account,
                    positions,
                )
                if cfg.enable_consecutive_loss_stop \
                        and count >= cfg.max_consecutive_losses:
                    if cfg.stop_for_day_after_loss_limit:
                        reason = (
                            f"consecutive-loss limit reached "
                            f"({count}/{cfg.max_consecutive_losses}); "
                            f"new entries locked for broker day"
                        )
                        self._lock_day(
                            "consecutive_losses", reason, account, positions,
                            close_positions=False)
                    else:
                        until = exit_time + cfg.loss_cooldown_minutes * 60
                        state["cooldown_until"] = max(
                            float(state.get("cooldown_until") or 0.0), until)
                        self._emit(
                            "warning",
                            f"Consecutive-loss cooldown activated until "
                            f"{datetime.fromtimestamp(until, timezone.utc).isoformat()}.",
                            account,
                            positions,
                        )
            elif net > 0.005:
                state["consecutive_losses"] = 0

        if changed:
            state["processed_position_ids"] = list(sorted(processed))[-1000:]
            self._save_state(force=True)

    # ------------------------------------------------------------------
    # Locks and protective actions
    # ------------------------------------------------------------------
    def _lock_day(self, kind: str, reason: str, account, positions,
                  close_positions: bool) -> None:
        if self._state.get("daily_lock"):
            return
        now = self._broker_now()
        self._state.update({
            "daily_lock": True,
            "lock_kind": kind,
            "lock_reason": reason,
            "locked_at": now.timestamp(),
            "protective_action_done": False,
        })
        self._save_state(force=True)
        self._emit("warning", f"SESSION LOCK: {reason}", account, positions)
        self._protect_positions(
            account, positions, close_positions=close_positions)

    def _protect_positions(self, account, positions,
                           close_positions: bool) -> None:
        if self._state.get("protective_action_done"):
            return

        if self.config.cancel_pending_on_stop:
            self._cancel_managed_pending_orders(account, positions)
        if close_positions:
            self._close_managed_positions(account, positions)

        self._state["protective_action_done"] = True
        self._save_state(force=True)

    def _cancel_managed_pending_orders(self, account, positions) -> None:
        fn = getattr(self._client, "orders_get", None)
        if fn is None:
            fn = getattr(getattr(self._client, "_mt5", None), "orders_get", None)
        remove_action = self._const("TRADE_ACTION_REMOVE", None)
        if fn is None or remove_action is None:
            return
        try:
            orders = fn() or []
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not inspect pending orders during session stop: %s", exc)
            return
        for order in orders:
            if int(getattr(order, "magic", 0) or 0) not in self._managed_magics:
                continue
            ticket = int(getattr(order, "ticket", 0) or 0)
            if ticket <= 0:
                continue
            result = self._client.order_send({
                "action": remove_action,
                "order": ticket,
                "comment": "Session guard cancel",
            })
            ok = getattr(result, "retcode", None) == self._const(
                "TRADE_RETCODE_DONE", getattr(self._client, "TRADE_RETCODE_DONE", None))
            self._emit(
                "info" if ok else "warning",
                f"Pending order {ticket} {'cancelled' if ok else 'cancel failed'} "
                f"after session stop.",
                account,
                positions,
            )

    def _close_managed_positions(self, account, positions) -> None:
        for position in list(positions or []):
            if int(getattr(position, "magic", 0) or 0) not in self._managed_magics:
                continue
            symbol = str(getattr(position, "symbol", "") or "")
            tick = self._client.symbol_info_tick(symbol)
            if tick is None:
                self._emit(
                    "warning",
                    f"Could not close position {getattr(position, 'ticket', 0)}: "
                    f"no tick for {symbol}.",
                    account,
                    positions,
                )
                continue
            is_buy = getattr(position, "type", None) == self._const(
                "POSITION_TYPE_BUY", getattr(self._client, "POSITION_TYPE_BUY", 0))
            request = {
                "action": self._const(
                    "TRADE_ACTION_DEAL",
                    getattr(self._client, "TRADE_ACTION_DEAL", 1)),
                "position": int(getattr(position, "ticket", 0) or 0),
                "symbol": symbol,
                "volume": float(getattr(position, "volume", 0.0) or 0.0),
                "type": self._const(
                    "ORDER_TYPE_SELL" if is_buy else "ORDER_TYPE_BUY",
                    getattr(
                        self._client,
                        "ORDER_TYPE_SELL" if is_buy else "ORDER_TYPE_BUY",
                        1 if is_buy else 0,
                    ),
                ),
                "price": float(tick.bid if is_buy else tick.ask),
                "deviation": 20,
                "magic": int(getattr(position, "magic", EXECUTION_MAGIC)
                             or EXECUTION_MAGIC),
                "comment": "Session guard close",
            }
            result = self._client.order_send(request)
            ok = getattr(result, "retcode", None) == self._const(
                "TRADE_RETCODE_DONE", getattr(self._client, "TRADE_RETCODE_DONE", None))
            ticket = request["position"]
            self._emit(
                "info" if ok else "warning",
                f"Position {ticket} {'closed' if ok else 'close failed'} "
                f"after session stop.",
                account,
                positions,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _build_managed_magics(self) -> set[int]:
        magics = {int(EXECUTION_MAGIC), int(CLOSE_MAGIC)}
        try:
            magics.update(int(book.magic) for book in build_books(self.settings))
        except Exception:  # noqa: BLE001
            pass
        return magics

    def _is_new_entry(self, request: dict) -> bool:
        action = request.get("action")
        if "position" in request or "position_by" in request:
            return False
        deal_action = self._const(
            "TRADE_ACTION_DEAL", getattr(self._client, "TRADE_ACTION_DEAL", None))
        pending_action = self._const("TRADE_ACTION_PENDING", None)
        return action == deal_action or (
            pending_action is not None and action == pending_action)

    def _record_local_entry(self, symbol: str) -> None:
        with self._lock:
            now = self._broker_now().timestamp()
            self._state["trades_today"] = int(
                self._state.get("trades_today") or 0) + 1
            per_symbol = dict(self._state.get("trades_by_symbol") or {})
            key = symbol.upper()
            per_symbol[key] = int(per_symbol.get(key, 0)) + 1
            self._state["trades_by_symbol"] = per_symbol
            self._state["last_entry_time"] = now
            self._save_state(force=True)

    def _broker_now(self) -> datetime:
        for symbol in getattr(self.settings, "symbols", ()) or (
                getattr(self.settings, "symbol", ""),):
            if not symbol:
                continue
            try:
                tick = self._client.symbol_info_tick(symbol)
            except Exception:  # noqa: BLE001
                tick = None
            ts = getattr(tick, "time", None)
            if ts:
                try:
                    return datetime.fromtimestamp(float(ts), timezone.utc)
                except (TypeError, ValueError, OSError):
                    pass
        return datetime.now(timezone.utc)

    def _const(self, name: str, default=None):
        if hasattr(self._client, name):
            return getattr(self._client, name)
        mt5 = getattr(self._client, "_mt5", None)
        return getattr(mt5, name, default) if mt5 is not None else default

    @staticmethod
    def _deal_timestamp(deal) -> float:
        time_msc = getattr(deal, "time_msc", None)
        if time_msc:
            return float(time_msc) / 1000.0
        return float(getattr(deal, "time", 0.0) or 0.0)

    @classmethod
    def _deal_sort_key(cls, deal):
        return (
            cls._deal_timestamp(deal),
            int(getattr(deal, "ticket", 0) or 0),
        )

    def _load_state(self) -> dict:
        path = self.config.state_path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_state(self, force: bool = False) -> None:
        now = datetime.now(timezone.utc).timestamp()
        if not force and now - self._last_persist < 1.0:
            return
        self._last_persist = now
        self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
        path = self.config.state_path
        try:
            directory = os.path.dirname(os.path.abspath(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("Could not persist session guard state to %s: %s", path, exc)

    def _emit(self, level: str, message: str, account=None, positions=None) -> None:
        logger = getattr(log, level, log.info)
        logger(message)
        if self.journal is None or account is None:
            return
        try:
            self.journal.log_risk_event(
                level != "warning",
                f"SESSION_GUARD: {message}",
                float(getattr(account, "balance", 0.0) or 0.0),
                float(getattr(account, "equity", 0.0) or 0.0),
                len(positions or []),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not write session guard event to journal: %s", exc)

    def _log_rejection(self, reason: str) -> None:
        now = datetime.now(timezone.utc).timestamp()
        # Avoid writing the same rejection every one-second strategy loop.
        if reason == self._last_reject_reason and now - self._last_reject_at < 60:
            return
        self._last_reject_reason = reason
        self._last_reject_at = now
        try:
            account = self._client.account_info()
            positions = self._client.positions_get() or []
        except Exception:  # noqa: BLE001
            account, positions = None, []
        self._emit("warning", f"ENTRY BLOCKED: {reason}", account, positions)
