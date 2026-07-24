from __future__ import annotations

import json

from mt5_ai_bridge.v14_21_scan_audit import ScanAuditJournal
from v14_3_satellite_bot_m1 import _pending_gold_bars


def test_scan_journal_persists_cursor_and_zero_candidate_event(tmp_path) -> None:
    cursors = tmp_path / "cursors.json"
    events = tmp_path / "events.jsonl"
    journal = ScanAuditJournal(cursors, events)
    journal.set_cursor("GOLD_M30", 200)
    journal.record(
        "GOLD_M30",
        "NO_SETUP",
        completed_bar_time=200,
        candidate_count=0,
        details={"evaluation_code": "NO_55_BAR_CHANNEL_BREAK"},
    )

    restored = ScanAuditJournal(cursors, events)
    assert restored.cursor("GOLD_M30") == 200
    latest = restored.snapshot()["latest_by_scope"]["GOLD_M30"]
    assert latest["outcome"] == "NO_SETUP"
    assert latest["candidate_count"] == 0
    assert json.loads(events.read_text(encoding="utf-8"))["details"][
        "evaluation_code"
    ] == "NO_55_BAR_CHANNEL_BREAK"


class GoldBarsClient:
    def copy_rates_from_pos(self, _symbol, timeframe, start, count):
        assert timeframe == "M30"
        assert start == 1
        del count
        return [{"time": 100}, {"time": 200}, {"time": 300}]


def test_pending_gold_bars_are_oldest_first_with_correct_shifts(tmp_path) -> None:
    journal = ScanAuditJournal(
        tmp_path / "cursors.json", tmp_path / "events.jsonl"
    )
    journal.set_cursor("GOLD_M30", 100)
    assert _pending_gold_bars(GoldBarsClient(), "XAUUSD", journal, 48) == [
        (200, 1),
        (300, 0),
    ]


def test_first_start_processes_latest_gold_bar_only(tmp_path) -> None:
    journal = ScanAuditJournal(
        tmp_path / "cursors.json", tmp_path / "events.jsonl"
    )
    assert _pending_gold_bars(GoldBarsClient(), "XAUUSD", journal, 48) == [
        (300, 0)
    ]
