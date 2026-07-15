from __future__ import annotations

import json
import os
from pathlib import Path

from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def test_dashboard_retries_transient_permission_error(tmp_path, monkeypatch) -> None:
    target = tmp_path / "dashboard.json"
    dashboard = WindowsSafeLiveDashboard(target)
    dashboard.replace_delay_seconds = 0

    real_replace = os.replace
    calls = {"count": 0}

    def flaky_replace(source, destination):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(source, destination)

    monkeypatch.setattr("v14_3_satellite_bot_windows.os.replace", flaky_replace)
    dashboard.write({"runner_status": "RUNNING", "candidate_count": 2})

    assert calls["count"] == 3
    assert json.loads(target.read_text(encoding="utf-8"))["candidate_count"] == 2


def test_dashboard_keeps_previous_snapshot_when_file_stays_locked(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "dashboard.json"
    target.write_text('{"runner_status":"RUNNING","candidate_count":1}', encoding="utf-8")
    dashboard = WindowsSafeLiveDashboard(target)
    dashboard.replace_attempts = 2
    dashboard.replace_delay_seconds = 0

    def locked_replace(_source, _destination):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr("v14_3_satellite_bot_windows.os.replace", locked_replace)
    dashboard.write({"runner_status": "RUNNING", "candidate_count":3})

    assert json.loads(target.read_text(encoding="utf-8"))["candidate_count"] == 1
    assert list(Path(tmp_path).glob("*.tmp")) == []
