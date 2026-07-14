"""V14.7 combined replay with the corrected GBPJPY protection policy.

V14.6 remains available as the profitability-preserving control. V14.7 keeps
its non-GBPJPY behavior while enforcing the forward-test correction:

* one unresolved GBPJPY ICT trade maximum;
* 0.20% normal and 0.10% post-loss GBPJPY risk;
* two GBPJPY losses stop the symbol for the UTC day;
* 0.50% symbol daily loss cap;
* two-loss rolling cooldown over four hours;
* one small win only reduces loss pressure by half;
* GBPJPY entries only from 07:00 through 19:59 UTC.

Research only. This module does not connect to MT5 or place orders.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from research.v14_6_profit_preserving_combined_replay import (
    DEFAULT_ICT,
    DEFAULT_V12,
    CombinedReplay,
    V146Config,
    load_ict,
    load_v12,
    safe_json,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "research" / "v14_7_gbpjpy_guarded_combined_out"


@dataclass(frozen=True)
class V147Config(V146Config):
    gbpjpy_normal_risk_percent: float = 0.20
    gbpjpy_post_loss_risk_percent: float = 0.10
    gbpjpy_max_open_positions: int = 1
    gbpjpy_win_pressure_recovery: float = 0.50
    gbpjpy_session_start_hour_utc: int = 7
    gbpjpy_session_end_hour_utc: int = 20
    symbol_daily_loss_cap_percent: float = 0.50
    symbol_pause_after_consecutive_losses: int = 2
    symbol_rolling_loss_count: int = 2
    symbol_rolling_loss_hours: float = 4.0
    symbol_stop_after_daily_losses: int = 2
    max_new_trades_per_symbol_hour: int = 1


class V147CombinedReplay(CombinedReplay):
    def __init__(self, v12: pd.DataFrame, ict: pd.DataFrame,
                 cfg: V147Config = V147Config()) -> None:
        super().__init__(v12, ict, cfg)
        self.gbpjpy_loss_pressure = 0.0
        self._gbpjpy_pressure_day = None

    @property
    def guarded_config(self) -> V147Config:
        return self.cfg  # type: ignore[return-value]

    def reset_day(self, ts: pd.Timestamp) -> None:
        day = pd.Timestamp(ts).date()
        if self._gbpjpy_pressure_day != day:
            self._gbpjpy_pressure_day = day
            self.gbpjpy_loss_pressure = 0.0
        super().reset_day(ts)

    def record_close(self, item: dict, pnl: float, ts: pd.Timestamp) -> None:
        super().record_close(item, pnl, ts)
        if str(item.get("symbol", "")).upper() != "GBPJPY":
            return
        if pnl < 0:
            self.gbpjpy_loss_pressure += 1.0
        elif pnl > 0:
            self.gbpjpy_loss_pressure = max(
                0.0,
                self.gbpjpy_loss_pressure
                - self.guarded_config.gbpjpy_win_pressure_recovery,
            )

    def ict_block_reason(self, row: dict) -> str | None:
        symbol = str(row.get("symbol", "")).upper()
        if symbol == "GBPJPY":
            entry = pd.Timestamp(row["entry_time"])
            hour = entry.hour
            if not (
                self.guarded_config.gbpjpy_session_start_hour_utc
                <= hour
                < self.guarded_config.gbpjpy_session_end_hour_utc
            ):
                return "GBPJPY_SESSION_BLOCK"
            open_count = sum(
                1 for item in self.open_ict
                if str(item.get("symbol", "")).upper() == "GBPJPY"
            )
            if open_count >= self.guarded_config.gbpjpy_max_open_positions:
                return "GBPJPY_ONE_POSITION_LIMIT"
        return super().ict_block_reason(row)

    def ict_risk_percent(self, row: dict) -> float:
        symbol = str(row.get("symbol", "")).upper()
        if symbol != "GBPJPY":
            return super().ict_risk_percent(row)
        reduced = (
            self.gbpjpy_loss_pressure > 0
            or self.day.symbol_daily_pnl["GBPJPY"] < 0
        )
        return (
            self.guarded_config.gbpjpy_post_loss_risk_percent
            if reduced
            else self.guarded_config.gbpjpy_normal_risk_percent
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run V14.7 V12 + ICT replay with corrected GBPJPY guard"
    )
    parser.add_argument("--v12-ledger", type=Path, default=DEFAULT_V12)
    parser.add_argument("--ict-trades", type=Path, default=DEFAULT_ICT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    replay = V147CombinedReplay(
        load_v12(args.v12_ledger),
        load_ict(args.ict_trades),
        V147Config(),
    )
    trades, skipped, events, summary = replay.run()
    summary["model"] = "V14.7_GBPJPY_GUARDED"
    summary["gbpjpy_corrections"] = asdict(V147Config())
    trades.to_csv(args.out / "v14_7_gbpjpy_guarded_trades.csv", index=False)
    skipped.to_csv(args.out / "v14_7_gbpjpy_guarded_skipped.csv", index=False)
    events.to_csv(args.out / "v14_7_gbpjpy_guarded_events.csv", index=False)
    (args.out / "v14_7_gbpjpy_guarded_summary.json").write_text(
        json.dumps(summary, indent=2, default=safe_json),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, default=safe_json))


if __name__ == "__main__":
    main()
