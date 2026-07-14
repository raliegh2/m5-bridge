from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from mt5_ai_bridge.symbol_diagnostics import build_diagnostics, next_scan_time


UTC = timezone.utc


class State:
    @staticmethod
    def guard_multiplier(_engine, _now):
        return 1.0


class Guard:
    @staticmethod
    def in_session(now):
        return 7 <= now.hour < 20

    @staticmethod
    def decision(open_positions=0, now=None):
        return SimpleNamespace(ok=open_positions == 0, risk_cap_percent=0.15 if open_positions == 0 else 0.0)


class Executor:
    state = State()
    gbpjpy_guard = Guard()

    @staticmethod
    def _positions(symbol=None):
        return []


class Adapter:
    executor = Executor()


class Client:
    def symbol_info_tick(self, _symbol):
        return SimpleNamespace(bid=1.0000, ask=1.0002)

    def symbol_info(self, symbol):
        return SimpleNamespace(digits=3 if symbol.endswith("JPY") else 5, point=0.001 if symbol.endswith("JPY") else 0.00001)


def frame(freq, periods, end):
    times = pd.date_range(end=end, periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame({"time": times, "end": times})


def test_diagnostics_contains_all_five_symbols_and_required_fields():
    now = datetime(2026, 7, 14, 10, 30, tzinfo=UTC)
    prepared = {
        symbol: (frame("1h", 120, now), frame("4h", 120, now), frame("1D", 120, now))
        for symbol in ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
    }
    candidates = pd.DataFrame([
        {
            "symbol": "GBPUSD",
            "engine": "GBPUSD_V10_PRECISION",
            "entry_time": pd.Timestamp(now),
        }
    ])
    diagnostics = build_diagnostics(
        Client(), Adapter(), prepared, candidates, now=now
    )

    assert len(diagnostics) == 5
    by_symbol = {item.symbol: item for item in diagnostics}
    assert by_symbol["GBPUSD"].rejection_code == "CANDIDATE_READY"
    assert by_symbol["EURUSD"].rejection_code == "NO_RECENT_CANDIDATE"
    assert by_symbol["GBPJPY"].current_spread_pips is not None
    assert by_symbol["AUDUSD"].bars == {"H1": 120, "H4": 120, "D1": 120}
    assert by_symbol["USDJPY"].next_eligible_scan_time


def test_next_scan_respects_gbpjpy_session_and_audusd_windows():
    late = datetime(2026, 7, 14, 21, 10, tzinfo=UTC)
    gbpjpy = next_scan_time("GBPJPY", late)
    assert gbpjpy.hour == 8
    assert gbpjpy.date().isoformat() == "2026-07-15"

    audusd = next_scan_time("AUDUSD", datetime(2026, 7, 14, 5, 0, tzinfo=UTC))
    assert audusd.hour == 8
