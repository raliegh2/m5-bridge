"""Localhost control server: serves the dashboard, streams live data as JSON,
and toggles trading on/off.

A small stdlib HTTP server (no extra dependencies). The bot loop reads
``ControlState.is_active()`` and only opens trades while active; the dashboard's
Start/Pause buttons POST to /start and /stop. The dashboard page polls /data
(a JSON snapshot the bot rewrites each loop) and patches itself in place, so it
updates in near-realtime without reloading the whole page.

Responses carry ``Access-Control-Allow-Origin: *`` so that a dashboard.html
opened directly as a local file (file://) can still fetch /data from the
server running on 127.0.0.1.

The listening socket sets ``allow_reuse_address = False`` so a SECOND bot cannot
silently share the port (a Windows SO_REUSEADDR foot-gun that leads to a "ghost"
old server answering some requests). A duplicate now fails to bind -> the app
prints a clear banner instead. A short bind retry still lets a normal restart
succeed once the previous process has released the port.

Request routing is factored into the pure ``route`` function so it can be
unit-tested without binding a socket.

Endpoints:
    GET  /            -> dashboard.html (full page shell)
    GET  /data        -> latest JSON snapshot (for the in-place live updates)
    GET  /state       -> {"active": bool}
    POST /start /stop -> toggle trading
    POST /prop/on /prop/off -> toggle prop-firm challenge mode
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple


class ControlState:
    """Thread-safe active/idle flag shared between the loop and the server."""

    def __init__(self, active: bool = False, prop: bool = False) -> None:
        self._active = bool(active)
        self._prop = bool(prop)
        self._lock = threading.Lock()

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def set_active(self, value: bool) -> None:
        with self._lock:
            self._active = bool(value)

    def is_prop(self) -> bool:
        """Whether prop-firm challenge mode is currently ON."""
        with self._lock:
            return self._prop

    def set_prop(self, value: bool) -> None:
        with self._lock:
            self._prop = bool(value)


def route(path: str, method: str, state: ControlState,
          dashboard_path: str, data_path: Optional[str] = None
          ) -> Tuple[int, str, bytes]:
    """Map an HTTP request to (status, content_type, body)."""
    p = path.split("?", 1)[0].rstrip("/") or "/"

    if p == "/start":
        state.set_active(True)
        return 200, "application/json", b'{"active": true}'
    if p == "/stop":
        state.set_active(False)
        return 200, "application/json", b'{"active": false}'
    if p == "/prop/on":
        state.set_prop(True)
        return 200, "application/json", b'{"prop": true}'
    if p == "/prop/off":
        state.set_prop(False)
        return 200, "application/json", b'{"prop": false}'
    if p == "/state":
        body = json.dumps({"active": state.is_active(),
                           "prop": state.is_prop()}).encode()
        return 200, "application/json", body
    if p == "/data":
        if data_path:
            try:
                with open(data_path, "rb") as fh:
                    return 200, "application/json; charset=utf-8", fh.read()
            except OSError:
                pass
        # No snapshot yet -> valid empty JSON so the page's poller no-ops.
        return 200, "application/json; charset=utf-8", b"{}"
    if p in ("/", "/index.html", "/dashboard.html"):
        try:
            with open(dashboard_path, "rb") as fh:
                return 200, "text/html; charset=utf-8", fh.read()
        except OSError:
            return 404, "text/plain", b"Dashboard not generated yet. Start the bot."
    return 404, "text/plain", b"Not found"


def _make_handler(state: ControlState, dashboard_path: str,
                  data_path: Optional[str] = None):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            status, ctype, body = route(self.path, method, state,
                                        dashboard_path, data_path)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # Allow a file:// dashboard (or any origin) to poll /data.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._respond("GET")

        def do_POST(self):
            self._respond("POST")

        def do_OPTIONS(self):  # CORS preflight (harmless; simple requests skip it)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):  # silence default stderr logging
            pass

    return Handler


class _ExclusiveServer(ThreadingHTTPServer):
    # Do NOT reuse the address: refuse to bind if another process already holds
    # the port, so we never silently run a duplicate ("ghost") server.
    allow_reuse_address = False


def start_control_server(state: ControlState, port: int = 8800,
                         dashboard_path: str = "dashboard.html",
                         host: str = "127.0.0.1",
                         data_path: Optional[str] = None,
                         bind_retries: int = 5) -> ThreadingHTTPServer:
    """Start the control server in a daemon thread; returns the server.

    Retries the bind a few times (covers a just-released port after a restart),
    then raises OSError if the port is genuinely taken by another process."""
    handler = _make_handler(state, dashboard_path, data_path)
    last_exc: Optional[OSError] = None
    for attempt in range(max(1, bind_retries)):
        server = _ExclusiveServer((host, port), handler, bind_and_activate=False)
        try:
            server.server_bind()
            server.server_activate()
        except OSError as exc:
            last_exc = exc
            try:
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
            if attempt < bind_retries - 1:
                time.sleep(1)
            continue
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server
    raise last_exc if last_exc else OSError("could not bind control server")
