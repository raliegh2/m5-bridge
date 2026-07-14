"""Strict GBPJPY admission filters layered over the persistent loss guard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .execution import pip_size
from .gbpjpy_guarded_execution import GBPJPYGuardedExecutor
from .v12_final_execution import ExecutionResult, FinalExecutionRequest


class GBPJPYStrictExecutor(GBPJPYGuardedExecutor):
    """Add pair-specific market-quality checks before GBPJPY execution."""

    def place(self, request: FinalExecutionRequest,
              now: Optional[datetime] = None) -> ExecutionResult:
        if request.symbol.upper() != "GBPJPY":
            return super().place(request, now=now)

        now_utc = now or datetime.now(timezone.utc)
        if not self.gbpjpy_guard.in_session(now_utc):
            cfg = self.gbpjpy_guard.config
            return ExecutionResult(
                False,
                "GBPJPY_SESSION_BLOCK",
                f"GBPJPY entries are limited to {cfg.session_start_hour_utc:02d}:00-"
                f"{cfg.session_end_hour_utc:02d}:00 UTC.",
            )

        cfg = self.gbpjpy_guard.config
        if not cfg.min_stop_pips <= request.stop_pips <= cfg.max_stop_pips:
            return ExecutionResult(
                False,
                "GBPJPY_STOP_RANGE_BLOCK",
                f"GBPJPY stop must be between {cfg.min_stop_pips:g} and "
                f"{cfg.max_stop_pips:g} pips.",
            )

        reward_risk = request.target_pips / request.stop_pips
        if reward_risk + 1e-12 < cfg.min_reward_risk:
            return ExecutionResult(
                False,
                "GBPJPY_REWARD_RISK_BLOCK",
                f"GBPJPY reward:risk {reward_risk:.2f} is below the "
                f"{cfg.min_reward_risk:.2f} minimum.",
            )

        pip = pip_size(self.client, request.symbol)
        tick = self.client.symbol_info_tick(request.symbol)
        if pip is None or pip <= 0 or tick is None:
            return ExecutionResult(
                False,
                "MARKET_DATA_UNAVAILABLE",
                "GBPJPY tick or pip data is unavailable.",
            )
        spread_pips = (float(tick.ask) - float(tick.bid)) / pip
        if spread_pips < 0 or spread_pips > cfg.max_spread_pips:
            return ExecutionResult(
                False,
                "GBPJPY_SPREAD_BLOCK",
                f"GBPJPY spread {spread_pips:.2f} pips exceeds the "
                f"{cfg.max_spread_pips:.2f}-pip limit.",
            )

        return super().place(request, now=now_utc)
