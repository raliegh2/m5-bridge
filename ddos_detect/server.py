"""Hardened HTTP server for the dashboard and its API.

Standard library only, so the attack surface is small and auditable. The
protections applied to every request:

* **Loopback binding by default**, refused otherwise unless explicitly
  overridden (see :class:`~ddos_detect.config.Settings`).
* **Host allow-list.** A localhost service is reachable from any web page that
  can resolve a name to 127.0.0.1, so requests whose ``Host`` header is not an
  expected value are rejected. This closes DNS rebinding.
* **Origin checking plus a per-session CSRF token** on every state-changing
  request, and ``SameSite=Strict`` HttpOnly session cookies.
* **Rate limiting**, tighter on ``/api/login`` than on the rest of the API.
* **A strict Content-Security-Policy** with no inline script and no external
  origins, plus the usual hardening headers.
* **Bounded request bodies** and JSON-only parsing.
* **Uniform error responses** - internal detail is logged, never returned.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import re
import sys
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .app import Application
from .auth import Principal
from .config import Settings
from .errors import (
    AuthenticationError,
    AuthorizationError,
    CaptureError,
    DdosDetectError,
    RateLimitError,
    ValidationError,
)
from .ratelimit import RateLimiter
from .simulate import SCENARIO_HELP, SCENARIOS, build_scenario
from .validation import validate_bool, validate_identifier, validate_int, validate_text

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
#: Only these files are servable. There is no path joining from user input, so
#: traversal is impossible by construction rather than by sanitisation.
STATIC_FILES = {
    "/": "dashboard.html",
    "/static/app.css": "app.css",
    "/static/app.js": "app.js",
}

MAX_BODY_BYTES = 64 * 1024
MAX_EVENT_STREAMS = 8
SESSION_COOKIE = "ddos_session"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), interest-cohort=()",
    "Cache-Control": "no-store",
}

_MONITOR_PATH = re.compile(r"^/api/monitors/([A-Za-z0-9_-]{1,64})$")
_MONITOR_METRICS_PATH = re.compile(r"^/api/monitors/([A-Za-z0-9_-]{1,64})/metrics$")
_ALERT_ACK_PATH = re.compile(r"^/api/alerts/([0-9]{1,12})/acknowledge$")
_AUTHZ_PATH = re.compile(r"^/api/authorizations/([0-9]{1,12})$")


class DashboardServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the application context."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, app: Application) -> None:
        self.app = app
        self.settings: Settings = app.settings
        self.api_limiter = RateLimiter(app.settings.api_requests_per_minute)
        self.login_limiter = RateLimiter(app.settings.login_requests_per_minute, burst=5)
        self.event_streams = threading.Semaphore(MAX_EVENT_STREAMS)
        self.started_at = time.time()
        super().__init__((app.settings.bind_host, app.settings.bind_port), DashboardHandler)
        # Derive the allow-list from the port actually bound, not the one
        # requested: port 0 means "any free port", and the configured value
        # would never match a real Host header.
        self.bound_port = int(self.server_address[1])
        self.allowed_hosts = _allowed_hosts(app.settings, self.bound_port)

    def handle_error(self, request, client_address) -> None:
        """Keep client disconnects out of the error log.

        A browser closing a keep-alive connection raises here on some
        platforms. Printing a traceback for it would bury the errors that
        actually matter.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, BrokenPipeError, TimeoutError)):
            log.debug("client %s disconnected: %s", client_address[0], exc)
            return
        log.exception("error handling request from %s", client_address[0])


def _allowed_hosts(settings: Settings, port: int) -> frozenset[str]:
    names = {"localhost", "127.0.0.1", "[::1]", "::1"}
    if not settings.is_loopback_bind:
        names.add(settings.bind_host)
    allowed = set()
    for name in names:
        allowed.add(name)
        allowed.add(f"{name}:{port}")
    return frozenset(allowed)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ddos-detect"
    sys_version = ""  # do not advertise the Python version
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------
    @property
    def app(self) -> Application:
        return self.server.app  # type: ignore[attr-defined]

    @property
    def settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        # Route through logging and never include query strings, which can
        # carry identifiers we would rather not persist in a terminal scrollback.
        log.info("%s %s", self.client_address[0], fmt % args)

    def log_error(self, fmt: str, *args: Any) -> None:
        log.warning("%s %s", self.client_address[0], fmt % args)

    # -- entry points ----------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain")

    def _dispatch(self, method: str) -> None:
        try:
            if not self._host_allowed():
                self._error(HTTPStatus.MISDIRECTED_REQUEST, "unexpected Host header")
                return
            parsed = urlparse(self.path)
            path = parsed.path
            if len(path) > 512:
                self._error(HTTPStatus.BAD_REQUEST, "path too long")
                return

            if method == "GET" and path in STATIC_FILES:
                self._serve_static(STATIC_FILES[path])
                return

            if not path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return

            self._rate_limit(path)
            if method in ("POST", "DELETE"):
                self._check_origin()
            handler = self._route(method, path)
            if handler is None:
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            handler(parsed)
        except RateLimitError as exc:
            self._error(HTTPStatus.TOO_MANY_REQUESTS, str(exc),
                        extra={"Retry-After": str(int(exc.retry_after) + 1)})
        except ValidationError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except AuthenticationError as exc:
            self._error(HTTPStatus.UNAUTHORIZED, str(exc))
        except AuthorizationError as exc:
            self._error(HTTPStatus.FORBIDDEN, str(exc))
        except CaptureError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except DdosDetectError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001 - never leak internals to the client
            log.exception("unhandled error serving %s", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def _route(self, method: str, path: str) -> Callable[[Any], None] | None:
        table: dict[tuple[str, str], Callable[[Any], None]] = {
            ("POST", "/api/login"): self._api_login,
            ("POST", "/api/logout"): self._api_logout,
            ("GET", "/api/session"): self._api_session,
            ("GET", "/api/overview"): self._api_overview,
            ("GET", "/api/preflight"): self._api_preflight,
            ("GET", "/api/monitors"): self._api_list_monitors,
            ("POST", "/api/monitors"): self._api_start_monitor,
            ("GET", "/api/alerts"): self._api_list_alerts,
            ("GET", "/api/authorizations"): self._api_list_authorizations,
            ("POST", "/api/authorizations"): self._api_grant_authorization,
            ("GET", "/api/audit"): self._api_audit,
            ("GET", "/api/events"): self._api_events,
            ("GET", "/api/scenarios"): self._api_scenarios,
            ("POST", "/api/simulate"): self._api_simulate,
        }
        direct = table.get((method, path))
        if direct is not None:
            return direct
        if method == "DELETE":
            match = _MONITOR_PATH.match(path)
            if match:
                return lambda parsed: self._api_stop_monitor(match.group(1))
            match = _AUTHZ_PATH.match(path)
            if match:
                return lambda parsed: self._api_revoke_authorization(match.group(1))
        if method == "GET":
            match = _MONITOR_METRICS_PATH.match(path)
            if match:
                return lambda parsed: self._api_monitor_metrics(match.group(1), parsed)
            match = _MONITOR_PATH.match(path)
            if match:
                return lambda parsed: self._api_monitor(match.group(1))
        if method == "POST":
            match = _ALERT_ACK_PATH.match(path)
            if match:
                return lambda parsed: self._api_acknowledge(match.group(1))
        return None

    # -- request helpers -------------------------------------------------
    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts  # type: ignore[attr-defined]

    def _check_origin(self) -> None:
        """Reject cross-origin state changes before touching any session."""
        origin = self.headers.get("Origin")
        if origin is None:
            return  # Same-origin fetch() omits Origin for some navigations.
        parsed = urlparse(origin)
        netloc = parsed.netloc.lower()
        if netloc not in self.server.allowed_hosts:  # type: ignore[attr-defined]
            raise AuthenticationError("cross-origin request rejected")

    def _rate_limit(self, path: str) -> None:
        key = f"{self.client_address[0]}"
        if path == "/api/login":
            self.server.login_limiter.check(key)  # type: ignore[attr-defined]
        self.server.api_limiter.check(key)  # type: ignore[attr-defined]

    def _body(self) -> dict:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise ValidationError("Content-Length is required")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ValidationError("invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            # The body is left unread, so this connection can no longer be
            # reused - the leftover bytes would be parsed as the next request.
            self.close_connection = True
            raise ValidationError(f"request body must be under {MAX_BODY_BYTES} bytes")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if length and ctype != "application/json":
            self.close_connection = True
            raise ValidationError("Content-Type must be application/json")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationError("body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValidationError("body must be a JSON object")
        return payload

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        try:
            cookie = SimpleCookie()
            cookie.load(raw)
        except Exception:  # noqa: BLE001 - a malformed cookie is simply absent
            return ""
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _principal(self, role: str = "viewer", *, csrf: bool = False) -> Principal:
        principal = self.app.auth.authenticate(self._cookie_token())
        if csrf:
            self.app.auth.check_csrf(principal, self.headers.get("X-CSRF-Token"))
        self.app.auth.require_role(principal, role)
        return principal

    def _client_label(self) -> str:
        agent = (self.headers.get("User-Agent") or "")[:80]
        return f"{self.client_address[0]} {agent}".strip()

    # -- responses -------------------------------------------------------
    def _send(self, status: HTTPStatus, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK,
              extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _error(self, status: HTTPStatus, message: str,
               extra: dict[str, str] | None = None) -> None:
        try:
            self._json({"error": message, "status": int(status)}, status, extra)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_static(self, filename: str) -> None:
        path = STATIC_DIR / filename
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, path.read_bytes(), ctype)

    # -- API: session ----------------------------------------------------
    def _api_login(self, parsed) -> None:
        payload = self._body()
        token, principal = self.app.auth.login(
            payload.get("username"), payload.get("password"), client=self._client_label()
        )
        # Reset the login bucket on success so a legitimate user who fat-fingered
        # their password a few times is not left throttled.
        self.server.login_limiter.reset(self.client_address[0])  # type: ignore[attr-defined]
        flags = [
            f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict",
            f"Max-Age={self.settings.session_ttl_seconds}",
        ]
        if self.settings.secure_cookies:
            flags.append("Secure")
        self._json(
            {"username": principal.username, "role": principal.role,
             "csrf_token": principal.csrf_token},
            extra={"Set-Cookie": "; ".join(flags)},
        )

    def _api_logout(self, parsed) -> None:
        token = self._cookie_token()
        actor = ""
        try:
            actor = self.app.auth.authenticate(token).username
        except AuthenticationError:
            pass
        if token:
            self.app.auth.logout(token, actor)
        expired = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        self._json({"ok": True}, extra={"Set-Cookie": expired})

    def _api_session(self, parsed) -> None:
        principal = self._principal()
        self._json({
            "username": principal.username, "role": principal.role,
            "csrf_token": principal.csrf_token,
        })

    # -- API: read -------------------------------------------------------
    def _api_overview(self, parsed) -> None:
        self._principal()
        self._json({
            "monitors": self.app.monitors.snapshots(),
            "alerts": self.app.store.list_alerts(limit=50),
            "authorizations": self.app.ledger.list(),
            "posture": self.app.preflight(),
            "uptime_seconds": time.time() - self.server.started_at,  # type: ignore[attr-defined]
        })

    def _api_preflight(self, parsed) -> None:
        self._principal()
        self._json(self.app.preflight())

    def _api_list_monitors(self, parsed) -> None:
        self._principal()
        self._json({"monitors": self.app.monitors.snapshots()})

    def _api_monitor(self, monitor_id: str) -> None:
        self._principal()
        monitor = self.app.monitors.get(validate_identifier(monitor_id, field="monitor"))
        if monitor is None:
            self._error(HTTPStatus.NOT_FOUND, "monitor not found")
            return
        self._json(monitor.snapshot())

    def _api_monitor_metrics(self, monitor_id: str, parsed) -> None:
        self._principal()
        ident = validate_identifier(monitor_id, field="monitor")
        params = parse_qs(parsed.query or "", keep_blank_values=False)
        limit = validate_int(
            (params.get("limit") or ["300"])[0], field="limit", minimum=1, maximum=5000,
            default=300,
        )
        self._json({"monitor_id": ident, "metrics": self.app.store.list_metrics(ident, limit)})

    def _api_list_alerts(self, parsed) -> None:
        self._principal()
        params = parse_qs(parsed.query or "", keep_blank_values=False)
        limit = validate_int((params.get("limit") or ["100"])[0], field="limit",
                             minimum=1, maximum=1000, default=100)
        active = validate_bool((params.get("active") or ["false"])[0], field="active",
                               default=False)
        self._json({"alerts": self.app.store.list_alerts(limit=limit, active_only=active)})

    def _api_list_authorizations(self, parsed) -> None:
        self._principal()
        params = parse_qs(parsed.query or "", keep_blank_values=False)
        include = validate_bool((params.get("all") or ["false"])[0], field="all", default=False)
        self._json({"authorizations": self.app.ledger.list(include_inactive=include)})

    def _api_audit(self, parsed) -> None:
        # The audit log records who watched whom; only admins may read it.
        self._principal("admin")
        params = parse_qs(parsed.query or "", keep_blank_values=False)
        limit = validate_int((params.get("limit") or ["100"])[0], field="limit",
                             minimum=1, maximum=1000, default=100)
        ok, detail = self.app.audit.verify_chain()
        self._json({
            "chain_ok": ok, "chain_detail": detail,
            "records": [
                {"seq": r.seq, "ts": r.ts, "actor": r.actor, "action": r.action,
                 "detail": r.detail}
                for r in self.app.audit.read(limit)
            ],
        })

    def _api_scenarios(self, parsed) -> None:
        self._principal()
        self._json({"scenarios": [{"name": n, "description": SCENARIO_HELP[n]}
                                  for n in SCENARIOS]})

    # -- API: write ------------------------------------------------------
    def _api_start_monitor(self, parsed) -> None:
        principal = self._principal("operator", csrf=True)
        payload = self._body()
        snapshot = self.app.monitors.start_monitor(
            payload.get("target"), principal.username,
            protocols=payload.get("protocols"), label=payload.get("label", ""),
        )
        self._json(snapshot, HTTPStatus.CREATED)

    def _api_stop_monitor(self, monitor_id: str) -> None:
        principal = self._principal("operator", csrf=True)
        ident = validate_identifier(monitor_id, field="monitor")
        if not self.app.monitors.stop_monitor(ident, principal.username):
            self._error(HTTPStatus.NOT_FOUND, "monitor not found")
            return
        self._json({"ok": True, "monitor_id": ident})

    def _api_grant_authorization(self, parsed) -> None:
        # Authorising a range is the system's most consequential action, so it
        # is admin-only regardless of who may start monitors.
        principal = self._principal("admin", csrf=True)
        payload = self._body()
        entry = self.app.ledger.grant(
            payload.get("cidr"), payload.get("justification"), principal.username,
            attestation=payload.get("attestation", False), days=payload.get("days"),
        )
        self._json(entry, HTTPStatus.CREATED)

    def _api_revoke_authorization(self, auth_id: str) -> None:
        principal = self._principal("admin", csrf=True)
        ident = validate_int(auth_id, field="id", minimum=1, maximum=2**31)
        if not self.app.ledger.revoke(ident, principal.username):
            self._error(HTTPStatus.NOT_FOUND, "authorization not found or already revoked")
            return
        self._json({"ok": True, "id": ident})

    def _api_acknowledge(self, alert_id: str) -> None:
        principal = self._principal("operator", csrf=True)
        ident = validate_int(alert_id, field="alert", minimum=1, maximum=2**31)
        if not self.app.store.acknowledge_alert(ident, principal.username):
            self._error(HTTPStatus.NOT_FOUND, "alert not found or already acknowledged")
            return
        self.app.audit.record("alert.acknowledged", actor=principal.username, alert=ident)
        self._json({"ok": True, "id": ident})

    def _api_simulate(self, parsed) -> None:
        """Start a monitor fed by generated traffic.

        This produces records in memory; nothing is transmitted. It exists so
        the detection pipeline can be demonstrated and verified without waiting
        for a real incident. The resulting monitor is flagged ``simulated``
        everywhere it appears.
        """
        principal = self._principal("operator", csrf=True)
        payload = self._body()
        scenario_name = validate_text(payload.get("scenario", "syn_flood"),
                                      field="scenario", max_len=32)
        if scenario_name not in SCENARIOS:
            raise ValidationError(f"unknown scenario; choose from {', '.join(SCENARIOS)}")
        speed = validate_int(payload.get("speed", 4), field="speed", minimum=1, maximum=20,
                             default=4)
        # The target is fixed to the scenario's documentation address rather
        # than taken from the caller. Simulation skips the authorization ledger
        # (there is no real traffic to authorise), so letting a caller choose
        # the target would turn that exemption into a way to create a monitor
        # entry naming an address nobody attested to.
        scenario = build_scenario(scenario_name)
        from .capture import ReplayCapture

        backend = ReplayCapture(self.settings, scenario.records, realtime=True, speed=speed)
        snapshot = self.app.monitors.start_monitor(
            scenario.target, principal.username,
            label=f"simulation: {scenario_name}", backend=backend,
        )
        self.app.audit.record(
            "simulation.started", actor=principal.username, scenario=scenario_name,
            target=scenario.target, packets=scenario.packet_count,
        )
        self._json({**snapshot, "scenario": scenario_name,
                    "packets": scenario.packet_count}, HTTPStatus.CREATED)

    # -- API: live event stream ------------------------------------------
    def _api_events(self, parsed) -> None:
        self._principal()
        semaphore = self.server.event_streams  # type: ignore[attr-defined]
        if not semaphore.acquire(blocking=False):
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "too many live streams open")
            return
        subscription = self.app.monitors.bus.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            self.close_connection = True
            deadline = time.time() + 3600
            while time.time() < deadline:
                try:
                    event = subscription.get(timeout=15.0)
                    chunk = f"data: {json.dumps(event, default=str)}\n\n"
                except queue.Empty:
                    chunk = ": keepalive\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.monitors.bus.unsubscribe(subscription)
            semaphore.release()


def serve(app: Application) -> DashboardServer:
    """Create the server. The caller runs ``serve_forever``."""
    server = DashboardServer(app)
    log.info(
        "dashboard listening on http://%s:%d (loopback_only=%s)",
        app.settings.bind_host, server.bound_port, app.settings.is_loopback_bind,
    )
    return server
