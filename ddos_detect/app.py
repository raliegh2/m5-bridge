"""Application assembly.

One place where settings, storage, the audit log, the authorization ledger,
authentication, and the monitor manager are wired together, so the CLI, the
HTTP server, and the tests all build the same object graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .audit import AuditLog
from .auth import AuthManager
from .authz import AuthorizationLedger
from .capture import CaptureBackend, capture_preflight
from .config import Settings
from .crypto import load_or_create_secret
from .engine import MonitorManager
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class Application:
    """The assembled system."""

    settings: Settings
    store: Store
    audit: AuditLog
    ledger: AuthorizationLedger
    auth: AuthManager
    monitors: MonitorManager
    secret: bytes

    @classmethod
    def build(cls, settings: Settings | None = None, *,
              backend_factory=None) -> "Application":
        settings = settings or Settings.from_env()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        secret = load_or_create_secret(settings.secret_path)
        store = Store(settings.db_path)
        audit = AuditLog(settings.audit_path, secret)
        ledger = AuthorizationLedger(store, settings, audit)
        auth = AuthManager(store, settings, audit)
        monitors = MonitorManager(
            settings, store, audit, ledger, secret, backend_factory=backend_factory
        )
        return cls(settings, store, audit, ledger, auth, monitors, secret)

    def preflight(self) -> dict:
        """Report the system's security posture and capture readiness."""
        info = capture_preflight(self.settings)
        chain_ok, chain_msg = self.audit.verify_chain()
        return {
            "capture": info,
            "bind": f"{self.settings.bind_host}:{self.settings.bind_port}",
            "loopback_only": self.settings.is_loopback_bind,
            "authorization_enforced": self.settings.require_authorization,
            "public_targets_allowed": self.settings.allow_public_targets,
            "anonymize_sources": self.settings.anonymize_sources,
            "retention_days": self.settings.retention_days,
            "header_only_snaplen": self.settings.snaplen,
            "audit_chain_ok": chain_ok,
            "audit_chain_detail": chain_msg,
            "active_monitors": self.monitors.count,
            "max_monitors": self.settings.max_monitors,
            "accounts_configured": self.auth.has_users(),
        }

    def close(self) -> None:
        try:
            self.monitors.stop_all()
        finally:
            self.store.close()


def simulated_backend(settings: Settings, scenario_records) -> CaptureBackend:
    """Build a replay backend for a generated scenario."""
    from .capture import ReplayCapture

    return ReplayCapture(settings, scenario_records, realtime=True)
