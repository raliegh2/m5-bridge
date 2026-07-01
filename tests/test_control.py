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
