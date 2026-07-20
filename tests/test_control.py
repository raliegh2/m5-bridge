"""Dashboard web server: ControlState + request routing (serves the page)."""

from mt5_ai_bridge.control import ControlState, route


def test_state_toggles():
    s = ControlState(active=False)
    assert not s.is_active()
    s.set_active(True)
    assert s.is_active()


def test_route_state_endpoint():
    s = ControlState(active=True)
    status, ctype, body = route("/state", "GET", s, "x.html")
    assert status == 200 and b'"active": true' in body


def test_route_serves_dashboard_file(tmp_path):
    f = tmp_path / "dash.html"
    f.write_text("<html>live</html>", encoding="utf-8")
    status, ctype, body = route("/", "GET", ControlState(), str(f))
    assert status == 200 and b"live" in body and "text/html" in ctype


def test_route_missing_dashboard_is_404(tmp_path):
    status, _, _ = route("/", "GET", ControlState(), str(tmp_path / "nope.html"))
    assert status == 404


def test_route_unknown_path_404():
    assert route("/whatever", "GET", ControlState(), "x.html")[0] == 404


def test_prop_state_toggles():
    s = ControlState(active=True, prop=False)
    assert not s.is_prop()
    s.set_prop(True)
    assert s.is_prop()


def test_route_prop_on_off():
    s = ControlState(active=True)
    status, _, body = route("/prop/on", "POST", s, "x.html")
    assert status == 200 and b'"prop": true' in body and s.is_prop()
    status, _, body = route("/prop/off", "POST", s, "x.html")
    assert status == 200 and b'"prop": false' in body and not s.is_prop()


def test_route_state_includes_prop():
    s = ControlState(active=True, prop=True)
    status, _, body = route("/state", "GET", s, "x.html")
    assert status == 200 and b'"prop": true' in body


def test_data_overlays_live_control_and_prop(tmp_path):
    import json
    snap = tmp_path / "dash.json"
    snap.write_text(json.dumps({
        "control": {"active": False, "prop": False},
        "prop": {"enabled": False, "status": "OFF"},
        "cards": {"balance": 100},
    }), encoding="utf-8")
    s = ControlState(active=True, prop=True)   # live state disagrees with file
    status, ctype, body = route("/data", "GET", s, "x.html", str(snap))
    assert status == 200
    data = json.loads(body)
    # Live flags win over the stale snapshot -> single click sticks.
    assert data["control"]["active"] is True and data["control"]["prop"] is True
    assert data["prop"]["enabled"] is True and data["prop"]["status"] != "OFF"
    # Non-control fields are preserved untouched.
    assert data["cards"]["balance"] == 100


def test_data_overlays_prop_off(tmp_path):
    import json
    snap = tmp_path / "dash.json"
    snap.write_text(json.dumps({
        "control": {"active": True, "prop": True},
        "prop": {"enabled": True, "status": "TRADING"},
    }), encoding="utf-8")
    s = ControlState(active=True, prop=False)
    _, _, body = route("/data", "GET", s, "x.html", str(snap))
    data = json.loads(body)
    assert data["prop"]["enabled"] is False and data["prop"]["status"] == "OFF"


def test_data_empty_snapshot_is_untouched(tmp_path):
    status, _, body = route("/data", "GET", ControlState(), "x.html",
                            str(tmp_path / "missing.json"))
    assert status == 200 and body == b"{}"
