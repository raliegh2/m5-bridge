"""Monitor lifecycle: capture in, alerts out.

A :class:`Monitor` owns one target: a capture backend feeding a
:class:`~ddos_detect.detector.Detector`, an evaluation loop that runs once a
second, and the alert records that result. :class:`MonitorManager` owns the
set of monitors, enforces policy on creation, and fans events out to dashboard
subscribers.

Policy enforced here, not at the UI layer, so the API and the CLI get the same
guarantees:

* a target is authorised through :class:`~ddos_detect.authz.AuthorizationLedger`
  before any socket is opened;
* the number of concurrent monitors is capped;
* a target may only be monitored once at a time;
* starting and stopping are both audited, with the authorisation that
  permitted them recorded alongside.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .audit import AuditLog
from .authz import AuthorizationLedger, Decision
from .capture import CaptureBackend, build_backend
from .config import Settings
from .crypto import pseudonymise
from .detector import (
    STATE_ATTACK,
    STATE_SUSPECTED,
    TRANSITION_CLOSED,
    TRANSITION_ESCALATED,
    TRANSITION_OPENED,
    TRANSITION_UPDATED,
    Detector,
    Evaluation,
)
from .errors import DdosDetectError, ValidationError
from .packets import PacketRecord
from .store import Store
from .validation import (
    address_scope,
    is_documentation_address,
    parse_ip,
    validate_protocols,
    validate_text,
)

log = logging.getLogger(__name__)

MAX_EVENT_BACKLOG = 200


@dataclass
class EventBus:
    """Fan-out of live events to dashboard subscribers.

    Each subscriber gets a bounded queue. A slow or stalled client drops its
    own oldest events rather than growing memory or blocking the evaluation
    loop - backpressure from a browser tab must never stall detection.
    """

    _subscribers: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=MAX_EVENT_BACKLOG)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


class Monitor:
    """One target under observation."""

    def __init__(self, monitor_id: str, target: str, protocols: Sequence[str], label: str,
                 created_by: str, settings: Settings, store: Store, audit: AuditLog,
                 backend: CaptureBackend, bus: EventBus, secret: bytes,
                 authorization: dict | None = None) -> None:
        self.id = monitor_id
        self.target = target
        self.protocols = tuple(protocols)
        self.label = label
        self.created_by = created_by
        self.authorization = authorization or {}
        self.created_at = time.time()
        self._settings = settings
        self._store = store
        self._audit = audit
        self._backend = backend
        self._bus = bus
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._alert_id: int | None = None
        self._last: Evaluation | None = None
        self._history: list[dict] = []

        formatter = (
            (lambda src: pseudonymise(src, secret))
            if settings.anonymize_sources else (lambda src: src)
        )
        self.detector = Detector(
            target=target,
            thresholds=settings.thresholds,
            bucket_seconds=settings.bucket_seconds,
            bucket_count=settings.bucket_count,
            learning_seconds=settings.learning_seconds,
            max_sources=settings.max_tracked_sources,
            top_talkers=settings.top_talkers,
            started_at=time.time(),
            source_formatter=formatter,
        )

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._backend.start(parse_ip(self.target), self.protocols, self._on_packet)
        self._thread = threading.Thread(
            target=self._evaluate_loop, name=f"evaluate-{self.id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._backend.stop()
        except Exception as exc:  # noqa: BLE001 - stopping must not raise
            log.warning("backend stop failed for %s: %s", self.id, exc)
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)
        with self._lock:
            if self._alert_id is not None:
                self._store.close_alert(self._alert_id)
                self._alert_id = None

    @property
    def simulated(self) -> bool:
        return self._backend.simulated

    # -- data flow -------------------------------------------------------
    def _on_packet(self, packet: PacketRecord) -> None:
        with self._lock:
            self.detector.observe(packet)

    def _evaluate_loop(self) -> None:
        interval = max(0.2, float(self._settings.evaluate_interval_seconds))
        while not self._stop.wait(interval):
            try:
                self._evaluate_once()
            except Exception as exc:  # noqa: BLE001 - one bad tick must not kill the loop
                log.exception("evaluation failed for %s: %s", self.id, exc)

    def _evaluate_once(self) -> None:
        now = time.time()
        with self._lock:
            evaluation = self.detector.evaluate(now)
            self._last = evaluation
        sample = {
            "ts": evaluation.ts,
            "pps": evaluation.metrics["pps"],
            "bps": evaluation.metrics["bps"],
            "syn_pps": evaluation.metrics["syn_pps"],
            "udp_pps": evaluation.metrics["udp_pps"],
            "icmp_pps": evaluation.metrics["icmp_pps"],
            "unique_sources": int(evaluation.metrics["unique_sources"]),
            "entropy": evaluation.metrics["entropy"],
            "score": evaluation.score,
            "state": evaluation.state,
        }
        self._store.add_metric(self.id, sample)
        with self._lock:
            self._history.append(sample)
            if len(self._history) > 600:
                del self._history[:-600]
        self._handle_transition(evaluation)
        self._bus.publish({
            "type": "metrics", "monitor_id": self.id, "target": self.target,
            "simulated": self.simulated, "sample": sample,
            "evaluation": evaluation.as_dict(),
        })

    def _handle_transition(self, ev: Evaluation) -> None:
        with self._lock:
            alert_id = self._alert_id
        active = ev.state in (STATE_SUSPECTED, STATE_ATTACK)
        evidence = {
            "signals": [
                {"name": s.name, "label": s.label, "score": round(s.score, 3), "detail": s.detail}
                for s in ev.signals
            ],
            "metrics": {k: (round(v, 3) if isinstance(v, float) else v)
                        for k, v in ev.metrics.items()},
            "advice": ev.advice,
            "state": ev.state,
        }
        sources = [{"source": src, "packets": count} for src, count in ev.top_sources]

        if active and alert_id is None:
            alert_id = self._store.open_alert(
                self.id, self.target, ev.classification, ev.severity, ev.score, evidence, sources
            )
            with self._lock:
                self._alert_id = alert_id
            self._audit.record(
                "alert.opened", actor="detector", monitor=self.id, target=self.target,
                classification=ev.classification, severity=ev.severity,
                score=round(ev.score, 3), simulated=self.simulated,
            )
            self._bus.publish({
                "type": "alert", "action": "opened", "monitor_id": self.id,
                "alert_id": alert_id, "evaluation": ev.as_dict(), "simulated": self.simulated,
            })
            return

        if active and alert_id is not None:
            self._store.update_alert(
                alert_id, classification=ev.classification, severity=ev.severity,
                score=ev.score, evidence=evidence, top_sources=sources,
            )
            if ev.transition == TRANSITION_ESCALATED:
                self._audit.record(
                    "alert.escalated", actor="detector", monitor=self.id, target=self.target,
                    alert=alert_id, severity=ev.severity, score=round(ev.score, 3),
                )
                self._bus.publish({
                    "type": "alert", "action": "escalated", "monitor_id": self.id,
                    "alert_id": alert_id, "evaluation": ev.as_dict(),
                    "simulated": self.simulated,
                })
            return

        if not active and alert_id is not None and ev.transition == TRANSITION_CLOSED:
            self._store.close_alert(alert_id)
            with self._lock:
                self._alert_id = None
            self._audit.record(
                "alert.closed", actor="detector", monitor=self.id, target=self.target,
                alert=alert_id,
            )
            self._bus.publish({
                "type": "alert", "action": "closed", "monitor_id": self.id,
                "alert_id": alert_id, "evaluation": ev.as_dict(), "simulated": self.simulated,
            })

    # -- introspection ---------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            last = self._last
            history = list(self._history[-180:])
        return {
            "id": self.id,
            "target": self.target,
            "label": self.label,
            "protocols": list(self.protocols),
            "created_by": self.created_by,
            "created_at": self.created_at,
            "simulated": self.simulated,
            "capture": self._backend.describe(),
            "authorization": self.authorization,
            "alert_id": self._alert_id,
            "evaluation": last.as_dict() if last else None,
            "history": history,
        }


class MonitorManager:
    """Owns the running monitors and the policy around creating them."""

    def __init__(self, settings: Settings, store: Store, audit: AuditLog,
                 ledger: AuthorizationLedger, secret: bytes,
                 backend_factory: Callable[[Settings], CaptureBackend] | None = None) -> None:
        self._settings = settings
        self._store = store
        self._audit = audit
        self._ledger = ledger
        self._secret = secret
        self._backend_factory = backend_factory or build_backend
        self._monitors: dict[str, Monitor] = {}
        self._lock = threading.RLock()
        self.bus = EventBus()
        self._maintenance = threading.Thread(
            target=self._maintenance_loop, name="retention", daemon=True
        )
        self._stop = threading.Event()
        self._maintenance.start()

    # -- lifecycle -------------------------------------------------------
    def start_monitor(self, target: object, actor: str, *, protocols: object = None,
                      label: object = "",
                      backend: CaptureBackend | None = None) -> dict:
        """Authorise and start monitoring ``target``."""
        addr = parse_ip(target, field="target")
        protos = validate_protocols(protocols)
        label_text = validate_text(label, field="label", max_len=80, required=False)

        # Authorisation happens before any resource is acquired.
        #
        # One narrow exemption: a simulated backend replays records built in
        # memory and opens no socket, so there is no traffic to be authorised
        # to observe. It is limited to documentation-range addresses, which by
        # definition cannot be anyone's real infrastructure, and it is audited
        # like any other decision.
        exempt = (
            backend is not None
            and backend.simulated
            and is_documentation_address(addr)
        )
        if exempt:
            decision = Decision(str(addr), address_scope(addr), None, None, None,
                                enforced=False)
            self._audit.record(
                "authorization.simulation_exempt", actor=actor, target=str(addr),
                reason="generated traffic against a documentation address",
            )
        else:
            decision = self._ledger.check(addr, actor=actor)

        with self._lock:
            if len(self._monitors) >= self._settings.max_monitors:
                raise ValidationError(
                    f"monitor limit reached ({self._settings.max_monitors}); "
                    "stop a monitor before starting another"
                )
            for monitor in self._monitors.values():
                if monitor.target == str(addr):
                    raise ValidationError(f"{addr} is already being monitored")

            monitor_id = uuid.uuid4().hex[:16]
            capture = backend or self._backend_factory(self._settings)
            monitor = Monitor(
                monitor_id=monitor_id, target=str(addr), protocols=protos, label=label_text,
                created_by=actor, settings=self._settings, store=self._store,
                audit=self._audit, backend=capture, bus=self.bus, secret=self._secret,
                authorization=decision.as_dict(),
            )
            try:
                monitor.start()
            except DdosDetectError:
                self._audit.record(
                    "monitor.start_failed", actor=actor, target=str(addr),
                )
                raise
            self._monitors[monitor_id] = monitor

        self._store.add_monitor(monitor_id, str(addr), protos, label_text, actor)
        self._audit.record(
            "monitor.started", actor=actor, monitor=monitor_id, target=str(addr),
            protocols=list(protos), scope=decision.scope,
            authorization=decision.authorization_id, enforced=decision.enforced,
            simulated=monitor.simulated, backend=capture.name,
        )
        self.bus.publish({"type": "monitor", "action": "started",
                          "monitor": monitor.snapshot()})
        return monitor.snapshot()

    def stop_monitor(self, monitor_id: str, actor: str) -> bool:
        with self._lock:
            monitor = self._monitors.pop(monitor_id, None)
        if monitor is None:
            return False
        monitor.stop()
        self._store.stop_monitor(monitor_id)
        self._audit.record(
            "monitor.stopped", actor=actor, monitor=monitor_id, target=monitor.target,
        )
        self.bus.publish({"type": "monitor", "action": "stopped", "monitor_id": monitor_id,
                          "target": monitor.target})
        return True

    def stop_all(self, actor: str = "system") -> None:
        for monitor_id in list(self._monitors):
            self.stop_monitor(monitor_id, actor)
        self._stop.set()

    # -- queries ---------------------------------------------------------
    def get(self, monitor_id: str) -> Monitor | None:
        with self._lock:
            return self._monitors.get(monitor_id)

    def snapshots(self) -> list[dict]:
        with self._lock:
            monitors = list(self._monitors.values())
        return [monitor.snapshot() for monitor in monitors]

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._monitors)

    # -- maintenance -----------------------------------------------------
    def _maintenance_loop(self) -> None:
        # Retention is enforced continuously rather than on demand, so
        # observational data about third parties ages out even if nobody
        # opens the dashboard.
        while not self._stop.wait(3600):
            try:
                removed = self._store.purge_expired(self._settings.retention_days)
                if any(removed.values()):
                    self._audit.record("retention.purged", actor="system", **removed)
            except Exception as exc:  # noqa: BLE001 - maintenance must not die
                log.warning("retention purge failed: %s", exc)
