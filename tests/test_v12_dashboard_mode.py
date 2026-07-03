from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from mt5_ai_bridge.v12_final_mode import AccountModeStore
import v12_final_dashboard as dashboard
from v12_final_dashboard import HTML, SharedStatus, choose_startup_mode, make_handler


def test_startup_prompt_selects_live(monkeypatch, tmp_path) -> None:
    store = AccountModeStore(str(tmp_path / "mode.json"))
    monkeypatch.setattr("builtins.input", lambda _prompt: "live")
    assert choose_startup_mode(store) == "LIVE"
    assert store.get() == "LIVE"


def test_dashboard_mode_endpoint_switches_shared_executor_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "DASHBOARD_STATE", tmp_path / "dashboard.json")
    store = AccountModeStore(str(tmp_path / "mode.json"))
    status = SharedStatus()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(status, store))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/mode",
            data=json.dumps({"mode": "LIVE"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:  # noqa: S310 - local test server
            payload = json.loads(response.read())
        assert payload == {
            "ok": True,
            "mode": "LIVE",
            "message": "Automatic execution mode switched to LIVE.",
        }
        assert store.get() == "LIVE"
        assert status.snapshot()["selected_account_mode"] == "LIVE"
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_renders_demo_and_live_controls() -> None:
    assert "setMode('DEMO')" in HTML
    assert "setMode('LIVE')" in HTML
    assert "BLOCKED: selected" in HTML
