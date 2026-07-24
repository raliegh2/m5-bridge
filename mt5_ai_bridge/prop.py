"""FTMO-style prop-firm risk guard: keep drawdown inside challenge limits.

Tracks the challenge starting balance and the equity high-water mark, and each
loop returns whether trading is allowed plus a risk multiplier that scales
position size DOWN as the account nears the daily-loss or max-drawdown limits.
This makes the bot far less likely to breach a prop-firm challenge.

Rules (all configurable):
- Max daily loss  (default 5%% of the challenge start balance)
- Max total drawdown (default 10%%; static from start, or trailing from the
  equity peak when ``trailing`` is on)
- Profit target   (default 8%%) -> once hit, stop opening new trades to lock it in
- De-risk: once a limit is ``derisk_start_pct`` used, per-trade risk scales
  linearly down to 0 at the limit.

State (start balance + equity peak + the day's opening equity) is persisted to a
small JSON file so a multi-day challenge survives restarts.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class PropConfig:
    enabled: bool = False
    start_balance: float = 0.0        # 0 -> capture the balance on first update
    max_daily_loss_pct: float = 5.0
    max_total_loss_pct: float = 10.0
    profit_target_pct: float = 8.0
    trailing: bool = False            # trailing max-drawdown from the equity peak
    derisk_start_pct: float = 60.0    # begin cutting risk at this %% of a limit
    state_path: str = "prop_state.json"


class PropGuard:
    """Stateful challenge tracker. ``update`` returns a status dict each loop."""

    def __init__(self, cfg: PropConfig):
        self.cfg = cfg
        self.start_balance = float(cfg.start_balance or 0.0)
        self.peak_equity = 0.0
        self.day = None
        self.day_start_equity = 0.0
        self._load()

    # --- persistence ---
    def _load(self) -> None:
        try:
            with open(self.cfg.state_path, "r", encoding="utf-8") as fh:
                s = json.load(fh)
            self.start_balance = self.start_balance or float(s.get("start_balance", 0.0))
            self.peak_equity = float(s.get("peak_equity", 0.0))
            self.day = s.get("day")
            self.day_start_equity = float(s.get("day_start_equity", 0.0))
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        try:
            tmp = self.cfg.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"start_balance": self.start_balance,
                           "peak_equity": self.peak_equity, "day": self.day,
                           "day_start_equity": self.day_start_equity}, fh)
            os.replace(tmp, self.cfg.state_path)
        except OSError:
            pass

    # --- core ---
    def update(self, balance: float, equity: float, today=None) -> dict:
        today = today or datetime.now(timezone.utc).date().isoformat()
        if self.start_balance <= 0:
            self.start_balance = float(balance)
        if equity > self.peak_equity:
            self.peak_equity = float(equity)
        if today != self.day:
            self.day = today
            self.day_start_equity = float(equity)
        self._save()

        sb = self.start_balance or 1.0
        cfg = self.cfg
        daily_loss_pct = max(0.0, 100.0 * (self.day_start_equity - equity) / sb)
        ref = self.peak_equity if cfg.trailing else sb
        total_dd_pct = max(0.0, 100.0 * (ref - equity) / sb)
        profit_pct = 100.0 * (equity - sb) / sb

        daily_used = (daily_loss_pct / cfg.max_daily_loss_pct
                      if cfg.max_daily_loss_pct > 0 else 0.0)
        total_used = (total_dd_pct / cfg.max_total_loss_pct
                      if cfg.max_total_loss_pct > 0 else 0.0)
        used = max(daily_used, total_used)

        target_hit = profit_pct >= cfg.profit_target_pct
        daily_breach = daily_loss_pct >= cfg.max_daily_loss_pct
        total_breach = total_dd_pct >= cfg.max_total_loss_pct
        allow = not (target_hit or daily_breach or total_breach)

        d = cfg.derisk_start_pct / 100.0
        if used <= d:
            scale = 1.0
        elif used >= 1.0:
            scale = 0.0
        else:
            scale = max(0.0, 1.0 - (used - d) / max(1e-9, 1.0 - d))
        if not allow:
            scale = 0.0

        if target_hit:
            status = "TARGET HIT"
        elif total_breach:
            status = "MAX DRAWDOWN"
        elif daily_breach:
            status = "DAILY LIMIT"
        elif scale < 1.0:
            status = "DE-RISKED"
        else:
            status = "TRADING"

        return {
            "enabled": True,
            "status": status,
            "allow_trading": allow,
            "risk_scale": round(scale, 3),
            "start_balance": round(sb, 2),
            "equity": round(float(equity), 2),
            "profit_pct": round(profit_pct, 2),
            "profit_target_pct": cfg.profit_target_pct,
            "daily_loss_pct": round(daily_loss_pct, 2),
            "max_daily_loss_pct": cfg.max_daily_loss_pct,
            "total_dd_pct": round(total_dd_pct, 2),
            "max_total_loss_pct": cfg.max_total_loss_pct,
            "trailing": cfg.trailing,
        }
