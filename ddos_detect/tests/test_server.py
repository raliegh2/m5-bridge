"""End-to-end HTTP tests, focused on the protections the server claims."""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from ddos_detect.server import SECURITY_HEADERS, DashboardServer
from ddos_detect.tests.conftest import ADMIN_PASSWORD

JUSTIFICATION = "Lab segment for the perimeter test, ticket OPS-4821"
VIEWER_PASSWORD = "seven-mountains-quiet-river"


class Client:
    """A minimal HTTP client that keeps the session cookie and CSRF token."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.host = f"127.0.0.1:{port}"
        self.cookie = ""
        self.csrf = ""

    def request(self, method: str, path: str, body=None, *, host=None, origin=None,
                csrf=..., content_type="application/json", raw_body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host if host is not None else self.host}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if origin is not None:
            headers["Origin"] = origin
        payload = raw_body
        if body is not None:
            payload = json.dumps(body).encode()
        if payload is not None:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(payload))
        if method in ("POST", "DELETE"):
            token = self.csrf if csrf is ... else csrf
            if token:
                headers["X-CSRF-Token"] = token
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            data = response.read()
            for key, value in response.getheaders():
                if key.lower() == "set-cookie" and value.startswith("ddos_session="):
                    self.cookie = value.split(";")[0]
            parsed = None
            if data:
                try:
                    parsed = json.loads(data)
                except ValueError:
                    parsed = None
            return response.status, parsed, dict(response.getheaders()), data
        finally:
            conn.close()

    def login(self, username="admin", password=ADMIN_PASSWORD):
        status, payload, _, _ = self.request("POST", "/api/login",
                                             {"username": username, "password": password})
        if status == 200:
            self.csrf = payload["csrf_token"]
        return status, payload


@pytest.fixture
def server(app, admin):
    app.auth.create_user("viewer1", VIEWER_PASSWORD, "viewer", actor="test")
    app.auth.create_user("op1", VIEWER_PASSWORD, "operator", actor="test")
    httpd = DashboardServer(app)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.fixture
def client(server) -> Client:
    return Client(server.bound_port)


@pytest.fixture
def admin_client(client) -> Client:
    assert client.login()[0] == 200
    return client


class TestAuthenticationGate:
    @pytest.mark.parametrize("path", [
        "/api/overview", "/api/monitors", "/api/alerts", "/api/authorizations",
        "/api/audit", "/api/session", "/api/preflight", "/api/events", "/api/scenarios",
    ])
    def test_reads_require_a_session(self, client, path):
        status, _, _, _ = client.request("GET", path)
        assert status == 401

    @pytest.mark.parametrize("method,path", [
        ("POST", "/api/monitors"), ("POST", "/api/authorizations"),
        ("POST", "/api/simulate"), ("DELETE", "/api/monitors/abc123"),
        ("DELETE", "/api/authorizations/1"), ("POST", "/api/alerts/1/acknowledge"),
    ])
    def test_writes_require_a_session(self, client, method, path):
        status, _, _, _ = client.request(method, path, {})
        assert status == 401

    def test_bad_credentials_are_rejected_uniformly(self, client):
        for username, password in [("admin", "wrong-password-here"), ("ghost", "whatever12345")]:
            status, payload = client.login(username, password)
            assert status == 401
            assert payload["error"] == "invalid username or password"

    def test_login_then_session_works(self, admin_client):
        status, payload, _, _ = admin_client.request("GET", "/api/session")
        assert status == 200
        assert payload["username"] == "admin"
        assert payload["role"] == "admin"

    def test_logout_invalidates_the_cookie(self, admin_client):
        assert admin_client.request("POST", "/api/logout", {})[0] == 200
        assert admin_client.request("GET", "/api/overview")[0] == 401

    def test_session_cookie_is_hardened(self, client):
        conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
        body = json.dumps({"username": "admin", "password": ADMIN_PASSWORD}).encode()
        conn.request("POST", "/api/login", body=body, headers={
            "Host": client.host, "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        })
        cookie = conn.getresponse().getheader("Set-Cookie")
        conn.close()
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Path=/" in cookie


class TestCsrfAndOrigin:
    def test_write_without_csrf_token_is_refused(self, admin_client):
        status, payload, _, _ = admin_client.request(
            "POST", "/api/authorizations",
            {"cidr": "10.10.0.0/16", "justification": JUSTIFICATION, "attestation": True},
            csrf="",
        )
        assert status == 401
        assert "CSRF" in payload["error"]

    def test_write_with_wrong_csrf_token_is_refused(self, admin_client):
        status, _, _, _ = admin_client.request(
            "POST", "/api/authorizations",
            {"cidr": "10.10.0.0/16", "justification": JUSTIFICATION, "attestation": True},
            csrf="not-the-right-token",
        )
        assert status == 401

    def test_cross_origin_write_is_refused(self, admin_client):
        status, _, _, _ = admin_client.request(
            "POST", "/api/authorizations",
            {"cidr": "10.10.0.0/16", "justification": JUSTIFICATION, "attestation": True},
            origin="https://evil.example",
        )
        assert status == 401

    def test_same_origin_write_succeeds(self, admin_client):
        status, payload, _, _ = admin_client.request(
            "POST", "/api/authorizations",
            {"cidr": "10.10.0.0/16", "justification": JUSTIFICATION, "attestation": True},
            origin=f"http://{admin_client.host}",
        )
        assert status == 201
        assert payload["cidr"] == "10.10.0.0/16"


class TestHostHeader:
    @pytest.mark.parametrize("host", [
        "evil.example", "attacker.test:8787", "", "127.0.0.1:1",
    ])
    def test_unexpected_host_is_refused(self, admin_client, host):
        # DNS rebinding: a page on any domain can reach a loopback service, so
        # the Host header must match what this server actually answers to.
        status, _, _, _ = admin_client.request("GET", "/api/overview", host=host)
        assert status == 421

    def test_localhost_alias_is_accepted(self, admin_client):
        status, _, _, _ = admin_client.request(
            "GET", "/api/overview", host=f"localhost:{admin_client.port}")
        assert status == 200


class TestRoles:
    def test_viewer_cannot_start_a_monitor(self, client):
        client.login("viewer1", VIEWER_PASSWORD)
        status, _, _, _ = client.request("POST", "/api/monitors", {"target": "10.10.0.5"})
        assert status == 401

    def test_viewer_cannot_grant_authorization(self, client):
        client.login("viewer1", VIEWER_PASSWORD)
        status, _, _, _ = client.request(
            "POST", "/api/authorizations",
            {"cidr": "10.10.0.0/16", "justification": JUSTIFICATION, "attestation": True})
        assert status == 401

    def test_operator_cannot_grant_authorization(self, client):
        # Starting a monitor and deciding who may be monitored are different
        # powers; only an admin holds the second.
        client.login("op1", VIEWER_PASSWORD)
        status, _, _, _ = client.request(
            "POST", "/api/authorizations",
            {"cidr": "10.10.0.0/16", "justification": JUSTIFICATION, "attestation": True})
        assert status == 401

    def test_viewer_cannot_read_the_audit_log(self, client):
        client.login("viewer1", VIEWER_PASSWORD)
        assert client.request("GET", "/api/audit")[0] == 401

    def test_admin_can_read_the_audit_log(self, admin_client):
        status, payload, _, _ = admin_client.request("GET", "/api/audit")
        assert status == 200
        assert payload["chain_ok"] is True


class TestMonitorPolicy:
    def test_unauthorized_target_is_refused(self, admin_client):
        status, payload, _, _ = admin_client.request(
            "POST", "/api/monitors", {"target": "10.10.0.5"})
        assert status == 403
        assert "authorization" in payload["error"]

    def test_public_target_is_refused(self, admin_client):
        status, payload, _, _ = admin_client.request(
            "POST", "/api/monitors", {"target": "8.8.8.8"})
        assert status == 403
        assert "public address" in payload["error"]

    @pytest.mark.parametrize("target", [
        "not-an-ip", "10.10.0.5/24", "10.10.0.5; ls", "", None, 42,
        "0.0.0.0", "224.0.0.1",
    ])
    def test_malformed_targets_are_refused(self, admin_client, target):
        status, _, _, _ = admin_client.request("POST", "/api/monitors", {"target": target})
        assert status in (400, 403)

    def test_authorized_target_starts_and_stops(self, admin_client):
        admin_client.request(
            "POST", "/api/authorizations",
            {"cidr": "10.10.0.0/16", "justification": JUSTIFICATION, "attestation": True})
        status, payload, _, _ = admin_client.request(
            "POST", "/api/monitors", {"target": "10.10.0.5", "protocols": ["tcp"]})
        # Capture needs privileges that CI does not have; either it started or
        # it failed for that reason, but authorization must have passed.
        assert status in (201, 503)
        if status == 201:
            assert payload["target"] == "10.10.0.5"
            stop = admin_client.request("DELETE", f"/api/monitors/{payload['id']}", {})
            assert stop[0] == 200

    def test_simulation_runs_without_privileges(self, admin_client):
        status, payload, _, _ = admin_client.request(
            "POST", "/api/simulate", {"scenario": "syn_flood", "speed": 20})
        assert status == 201
        assert payload["simulated"] is True
        admin_client.request("DELETE", f"/api/monitors/{payload['id']}", {})

    def test_unknown_simulation_scenario_is_refused(self, admin_client):
        status, _, _, _ = admin_client.request("POST", "/api/simulate", {"scenario": "nuke"})
        assert status == 400


class TestRequestHandling:
    def test_security_headers_are_present(self, admin_client):
        _, _, headers, _ = admin_client.request("GET", "/api/overview")
        for key, value in SECURITY_HEADERS.items():
            assert headers.get(key) == value

    def test_csp_forbids_inline_script_and_remote_origins(self):
        csp = SECURITY_HEADERS["Content-Security-Policy"]
        assert "'unsafe-inline'" not in csp
        assert "'unsafe-eval'" not in csp
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_dashboard_is_served(self, client):
        status, _, headers, body = client.request("GET", "/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"<title>DDoS Detection</title>" in body

    def test_static_assets_are_served(self, client):
        for path in ("/static/app.css", "/static/app.js"):
            status, _, _, body = client.request("GET", path)
            assert status == 200
            assert body

    @pytest.mark.parametrize("path", [
        "/static/../server.py", "/static/%2e%2e/config.py", "/static/ddos_detect.db",
        "/etc/passwd", "/api/nope", "/static/", "/..%2f..%2fconfig.py",
    ])
    def test_unlisted_paths_are_not_served(self, client, path):
        status, _, _, _ = client.request("GET", path)
        assert status in (400, 404)

    def test_oversized_body_is_refused(self, admin_client):
        big = json.dumps({"target": "10.0.0.1", "label": "x" * 200_000}).encode()
        status, _, _, _ = admin_client.request("POST", "/api/monitors", raw_body=big)
        assert status == 400

    def test_wrong_content_type_is_refused(self, admin_client):
        status, _, _, _ = admin_client.request(
            "POST", "/api/monitors", raw_body=b"target=10.0.0.1",
            content_type="application/x-www-form-urlencoded")
        assert status == 400

    def test_malformed_json_is_refused(self, admin_client):
        status, _, _, _ = admin_client.request("POST", "/api/monitors", raw_body=b"{not json")
        assert status == 400

    def test_json_array_body_is_refused(self, admin_client):
        status, _, _, _ = admin_client.request("POST", "/api/monitors", raw_body=b"[1,2,3]")
        assert status == 400

    def test_errors_do_not_leak_internals(self, admin_client):
        status, payload, _, _ = admin_client.request("POST", "/api/monitors",
                                                     {"target": "not-an-ip"})
        assert status == 400
        assert "Traceback" not in json.dumps(payload)
        assert "/ddos_detect/" not in json.dumps(payload)

    def test_server_does_not_advertise_its_python_version(self, client):
        _, _, headers, _ = client.request("GET", "/")
        assert "Python" not in headers.get("Server", "")


class TestRateLimiting:
    def test_login_attempts_are_throttled(self, client):
        statuses = [client.login("admin", "wrong-password-here")[0] for _ in range(15)]
        assert 429 in statuses

    def test_throttled_response_carries_retry_after(self, client):
        for _ in range(15):
            client.login("admin", "wrong-password-here")
        status, _, headers, _ = client.request(
            "POST", "/api/login", {"username": "admin", "password": "wrong-password-here"})
        assert status == 429
        assert int(headers["Retry-After"]) >= 1
