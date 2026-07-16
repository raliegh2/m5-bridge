"""V14.4 profit-guarded live executor.

Wraps :class:`ReconciledResearchParityLiveExecutor` with the live-cost and
live-expectancy protections from :mod:`v14_4_profit_guard`. All V14.3
admission controls (setup risk tiers, symbol guards, portfolio caps,
drawdown governor, demo-only transmission) still run unchanged after these
checks; this layer can only make the bot trade smaller or skip trades, never
larger.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .v14_3_live_execution import ExecutionResult, LiveSignal, pip_size
from .v14_3_position_reconciliation import (
    ReconciledResearchParityLiveExecutor,
    ReconciledResearchParityState,
)
from .v14_3_research_parity_execution import ResearchParityLiveRunnerConfig
from .v14_4_profit_guard import (
    ProfitGuardConfig,
    apply_expectancy_tier,
    expectancy_tier,
    reconstruct_peak_balance,
    setup_key,
    spread_cost_reason,
)


class ProfitGuardedState(ReconciledResearchParityState):
    """Parity state extended with per-setup live R history."""

    def _default(self) -> dict[str, Any]:
        payload = super()._default()
        payload.setdefault("setup_stats", {})
        payload.setdefault("peak_seeded_at", None)
        return payload

    def record_closed(
        self,
        position: dict[str, Any],
        pnl: float,
        closed_at: datetime,
    ) -> None:
        risk_dollars = float(position.get("risk_dollars", 0.0) or 0.0)
        if risk_dollars > 0 and str(position.get("mode", "")).upper() == "ICT":
            key = setup_key(
                str(position.get("symbol", "")),
                str(position.get("setup", "")),
            )
            stats = self.data.setdefault("setup_stats", {})
            results = stats.setdefault(key, [])
            results.append(round(float(pnl) / risk_dollars, 4))
            # Keep a bounded history; the tracker only reads a rolling window.
            if len(results) > 100:
                stats[key] = results[-100:]
        super().record_closed(position, pnl, closed_at)

    def setup_results(self, symbol: str, setup: str) -> list[float]:
        return [
            float(value)
            for value in self.data.get("setup_stats", {}).get(
                setup_key(symbol, setup), []
            )
        ]

    def seed_peak_equity(self, value: float, now: datetime) -> None:
        self.data["peak_equity"] = max(
            float(self.data.get("peak_equity", 0.0) or 0.0),
            float(value),
        )
        self.data["peak_seeded_at"] = now.astimezone(timezone.utc).isoformat()
        self.save()

    @property
    def peak_seeded(self) -> bool:
        return bool(self.data.get("peak_seeded_at"))


class ProfitGuardedLiveExecutor(ReconciledResearchParityLiveExecutor):
    """Research-parity executor with V14.4 live profitability guards."""

    def __init__(
        self,
        client: Any,
        config: ResearchParityLiveRunnerConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
        guard_config: Optional[ProfitGuardConfig] = None,
    ) -> None:
        super().__init__(client, config, approval_callback)
        self.guard = guard_config or ProfitGuardConfig.from_env()
        self.state = ProfitGuardedState(config.state_path)

    # ------------------------------------------------------------------
    # Peak-equity integrity
    # ------------------------------------------------------------------
    def _ensure_peak_seeded(self, account: Any, now: datetime) -> None:
        if self.state.peak_seeded:
            return
        balance = float(getattr(account, "balance", 0.0) or 0.0)
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        peak = reconstruct_peak_balance(
            self.client,
            balance,
            self.guard.peak_reconstruction_days,
            now=now,
        )
        self.state.seed_peak_equity(max(peak, equity), now)

    # ------------------------------------------------------------------
    # Setup-level live expectancy
    # ------------------------------------------------------------------
    def _setup_tier(self, signal: LiveSignal) -> str:
        return expectancy_tier(
            self.state.setup_results(signal.symbol, signal.setup),
            self.guard,
        )

    def _ict_admission_risk(
        self,
        signal: LiveSignal,
        drawdown_percent: float,
    ) -> float:
        base = super()._ict_admission_risk(signal, drawdown_percent)
        return apply_expectancy_tier(base, self._setup_tier(signal), self.guard)

    # ------------------------------------------------------------------
    # Pre-admission guards
    # ------------------------------------------------------------------
    def _daily_loss_stop_reason(self, account: Any) -> str | None:
        day = self.state.data.get("day", {})
        start_equity = float(day.get("start_equity", 0.0) or 0.0)
        if start_equity <= 0:
            return None
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        loss_percent = (start_equity - equity) / start_equity * 100.0
        if loss_percent >= self.guard.daily_loss_stop_percent:
            return (
                f"Account is down {loss_percent:.2f}% from day-start equity"
                f" {start_equity:.2f}; V14.4 daily loss stop is"
                f" {self.guard.daily_loss_stop_percent:.2f}%"
            )
        return None

    def _m1_staleness_reason(
        self,
        signal: LiveSignal,
        now: datetime,
    ) -> str | None:
        timeframe = str(signal.metadata.get("timeframe", "")).upper()
        if timeframe != "M1":
            return None
        age = now - signal.signal_time.astimezone(timezone.utc)
        if age > timedelta(minutes=self.guard.max_m1_signal_age_minutes):
            return (
                f"M1 scalp signal is {age.total_seconds() / 60.0:.1f} minutes"
                f" old; V14.4 limit is"
                f" {self.guard.max_m1_signal_age_minutes:.1f} minutes"
            )
        return None

    def _spread_cost_reason(self, signal: LiveSignal) -> str | None:
        info = self.client.symbol_info(signal.broker_symbol)
        tick = self.client.symbol_info_tick(signal.broker_symbol)
        if info is None or tick is None:
            return None  # Parity path reports MARKET_DATA_UNAVAILABLE itself.
        pip = pip_size(info, signal.symbol)
        if pip <= 0:
            return None
        spread_pips = (float(tick.ask) - float(tick.bid)) / pip
        return spread_cost_reason(spread_pips, float(signal.stop_pips), self.guard)

    def place(
        self,
        signal: LiveSignal,
        now: Optional[datetime] = None,
    ) -> ExecutionResult:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        if signal.key in self.state.data.get("seen", {}):
            # Let the parity path report DUPLICATE_SIGNAL without paying for
            # broker queries on every re-evaluated candidate.
            return super().place(signal, now=now)

        account = self.client.account_info()
        if account is not None:
            self._ensure_peak_seeded(account, now)
            self.state.reset_day(now, float(getattr(account, "equity", 0.0) or 0.0))

        reason = self._m1_staleness_reason(signal, now)
        if reason is not None:
            return ExecutionResult(False, "STALE_M1_SIGNAL", reason)

        if account is not None:
            reason = self._daily_loss_stop_reason(account)
            if reason is not None:
                return ExecutionResult(False, "V14_4_DAILY_LOSS_STOP", reason)

        reason = self._spread_cost_reason(signal)
        if reason is not None:
            return ExecutionResult(False, "V14_4_SPREAD_COST_GUARD", reason)

        return super().place(signal, now=now)

    def profit_guard_snapshot(self) -> dict[str, Any]:
        """Diagnostics for the dashboard and preflight."""
        stats = self.state.data.get("setup_stats", {})
        return {
            "config": {
                "max_spread_fraction_of_stop": self.guard.max_spread_fraction_of_stop,
                "max_m1_signal_age_minutes": self.guard.max_m1_signal_age_minutes,
                "daily_loss_stop_percent": self.guard.daily_loss_stop_percent,
                "expectancy_window": self.guard.expectancy_window,
                "reduce_threshold_r": self.guard.reduce_threshold_r,
                "observe_threshold_r": self.guard.observe_threshold_r,
            },
            "setup_tiers": {
                key: expectancy_tier(
                    [float(value) for value in results],
                    self.guard,
                )
                for key, results in sorted(stats.items())
            },
            "peak_equity": float(self.state.data.get("peak_equity", 0.0) or 0.0),
            "peak_seeded_at": self.state.data.get("peak_seeded_at"),
        }
