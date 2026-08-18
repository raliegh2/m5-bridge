"""The full pipeline: capture backend -> detector -> alerts -> storage."""

from __future__ import annotations

import time

import pytest

from ddos_detect.capture import ReplayCapture
from ddos_detect.errors import AuthorizationError, ValidationError
from ddos_detect.packets import PROTO_TCP, SYN, PacketRecord
from ddos_detect.simulate import build_scenario

JUSTIFICATION = "Lab segment for the perimeter test, ticket OPS-4821"


def authorize(app, cidr="192.0.2.0/24"):
    return app.ledger.grant(cidr, JUSTIFICATION, "admin", attestation=True)


def wait_for(predicate, timeout=20.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestPolicy:
    def test_unauthorized_target_never_opens_a_backend(self, app):
        backend = ReplayCapture(app.settings, [], realtime=False)
        with pytest.raises(AuthorizationError):
            app.monitors.start_monitor("10.99.0.1", "operator", backend=backend)
        assert backend.running is False
        assert app.monitors.count == 0

    def test_duplicate_target_is_refused(self, app):
        authorize(app)
        app.monitors.start_monitor(
            "192.0.2.10", "operator", backend=ReplayCapture(app.settings, [], realtime=False))
        with pytest.raises(ValidationError, match="already being monitored"):
            app.monitors.start_monitor(
                "192.0.2.10", "operator",
                backend=ReplayCapture(app.settings, [], realtime=False))

    def test_monitor_limit_is_enforced(self, app):
        authorize(app)
        for i in range(app.settings.max_monitors):
            app.monitors.start_monitor(
                f"192.0.2.{i + 1}", "operator",
                backend=ReplayCapture(app.settings, [], realtime=False))
        with pytest.raises(ValidationError, match="limit reached"):
            app.monitors.start_monitor(
                "192.0.2.200", "operator",
                backend=ReplayCapture(app.settings, [], realtime=False))

    def test_start_and_stop_are_audited(self, app):
        authorize(app)
        snapshot = app.monitors.start_monitor(
            "192.0.2.10", "operator", backend=ReplayCapture(app.settings, [], realtime=False))
        app.monitors.stop_monitor(snapshot["id"], "operator")
        actions = [r.action for r in app.audit.read(50)]
        assert "monitor.started" in actions
        assert "monitor.stopped" in actions
        assert app.audit.verify_chain()[0] is True

    def test_snapshot_records_the_authorization_used(self, app):
        # A real (non-documentation) target goes through the ledger, and the
        # monitor carries the entry that permitted it.
        entry = authorize(app, "10.10.0.0/16")
        snapshot = app.monitors.start_monitor(
            "10.10.0.5", "operator", backend=ReplayCapture(app.settings, [], realtime=False))
        assert snapshot["authorization"]["authorization_id"] == entry["id"]
        assert snapshot["authorization"]["cidr"] == "10.10.0.0/16"
        assert snapshot["authorization"]["enforced"] is True


class TestDetectionPipeline:
    def test_attack_traffic_produces_a_stored_alert(self, app):
        authorize(app)
        scenario = build_scenario("syn_flood", "192.0.2.10", duration=140.0,
                                  attack_start=5.0, attack_duration=40.0)
        backend = ReplayCapture(app.settings, scenario.records, realtime=True, speed=20)
        snapshot = app.monitors.start_monitor(
            "192.0.2.10", "operator", backend=backend, label="pipeline test")

        assert wait_for(lambda: app.store.list_alerts(monitor_id=snapshot["id"])), \
            "no alert was stored for a sustained SYN flood"
        alert = app.store.list_alerts(monitor_id=snapshot["id"])[0]
        assert alert["classification"] == "syn_flood"
        assert alert["severity"] in ("low", "medium", "high", "critical")
        assert alert["evidence"]["advice"]
        assert alert["evidence"]["signals"]
        app.monitors.stop_monitor(snapshot["id"], "operator")

    def test_metrics_are_persisted(self, app):
        authorize(app)
        scenario = build_scenario("baseline", "192.0.2.10", duration=30.0)
        backend = ReplayCapture(app.settings, scenario.records, realtime=True, speed=20)
        snapshot = app.monitors.start_monitor("192.0.2.10", "operator", backend=backend)
        assert wait_for(lambda: len(app.store.list_metrics(snapshot["id"])) >= 3)
        sample = app.store.list_metrics(snapshot["id"])[-1]
        assert sample["pps"] >= 0
        assert sample["state"] in ("learning", "normal", "suspected", "attack", "recovering")
        app.monitors.stop_monitor(snapshot["id"], "operator")

    def test_stopping_closes_an_open_alert(self, app):
        authorize(app)
        scenario = build_scenario("syn_flood", "192.0.2.10", duration=140.0,
                                  attack_start=5.0, attack_duration=120.0)
        backend = ReplayCapture(app.settings, scenario.records, realtime=True, speed=20)
        snapshot = app.monitors.start_monitor("192.0.2.10", "operator", backend=backend)
        assert wait_for(lambda: app.store.list_alerts(monitor_id=snapshot["id"],
                                                      active_only=True))
        app.monitors.stop_monitor(snapshot["id"], "operator")
        assert app.store.list_alerts(monitor_id=snapshot["id"], active_only=True) == []

    def test_events_reach_subscribers(self, app):
        authorize(app)
        subscription = app.monitors.bus.subscribe()
        scenario = build_scenario("baseline", "192.0.2.10", duration=20.0)
        backend = ReplayCapture(app.settings, scenario.records, realtime=True, speed=20)
        snapshot = app.monitors.start_monitor("192.0.2.10", "operator", backend=backend)
        event = subscription.get(timeout=10)
        assert event["type"] in ("monitor", "metrics")
        app.monitors.stop_monitor(snapshot["id"], "operator")
        app.monitors.bus.unsubscribe(subscription)

    def test_slow_subscriber_does_not_block_publishing(self, app):
        # A stalled browser tab must never be able to stall detection.
        bus = app.monitors.bus
        subscription = bus.subscribe()
        for i in range(1000):
            bus.publish({"type": "metrics", "n": i})
        assert subscription.qsize() <= 200
        bus.unsubscribe(subscription)


class TestSimulationExemption:
    def test_documentation_address_is_exempt_but_audited(self, app):
        backend = ReplayCapture(app.settings, [], realtime=False)
        snapshot = app.monitors.start_monitor("192.0.2.10", "operator", backend=backend)
        assert snapshot["simulated"] is True
        assert snapshot["authorization"]["enforced"] is False
        assert any(r.action == "authorization.simulation_exempt" for r in app.audit.read(20))

    def test_exemption_does_not_extend_to_real_addresses(self, app):
        # A simulated backend is not a way around the ledger for a real target.
        backend = ReplayCapture(app.settings, [], realtime=False)
        with pytest.raises(AuthorizationError):
            app.monitors.start_monitor("10.4.5.6", "operator", backend=backend)

    def test_exemption_does_not_extend_to_live_backends(self, app):
        from ddos_detect.capture import RawSocketCapture

        with pytest.raises(AuthorizationError):
            app.monitors.start_monitor(
                "10.4.5.6", "operator", backend=RawSocketCapture(app.settings))


class TestAnonymisation:
    def test_sources_can_be_pseudonymised(self, tmp_path):
        from ddos_detect.app import Application
        from ddos_detect.config import Settings

        settings = Settings(data_dir=tmp_path / "d", bind_port=0, kdf_iterations=1000,
                            anonymize_sources=True, learning_seconds=0)
        app = Application.build(settings)
        try:
            app.ledger.grant("192.0.2.0/24", JUSTIFICATION, "admin", attestation=True)
            records = [
                PacketRecord(time.time(), "198.51.100.7", "192.0.2.10", PROTO_TCP, 60,
                             src_port=1234, dst_port=443, tcp_flags=SYN)
                for _ in range(50)
            ]
            backend = ReplayCapture(settings, records, realtime=False)
            snapshot = app.monitors.start_monitor("192.0.2.10", "operator", backend=backend)
            assert wait_for(lambda: app.monitors.get(snapshot["id"]).snapshot()["evaluation"])
            evaluation = app.monitors.get(snapshot["id"]).snapshot()["evaluation"]
            sources = [s["source"] for s in evaluation["top_sources"]]
            assert sources, "no sources were recorded"
            assert all(s.startswith("anon:") for s in sources)
            assert "198.51.100.7" not in sources
        finally:
            app.close()


class TestRetention:
    def test_purge_removes_aged_data(self, app):
        old = time.time() - 30 * 86400
        app.store.add_metric("m1", {
            "ts": old, "pps": 1, "bps": 1, "syn_pps": 0, "udp_pps": 0, "icmp_pps": 0,
            "unique_sources": 1, "entropy": 0.0, "score": 0.0, "state": "normal",
        })
        alert_id = app.store.open_alert("m1", "10.0.0.1", "syn_flood", "high", 0.9, {}, [])
        app.store._execute("UPDATE alerts SET started_at = ? WHERE id = ?", (old, alert_id))
        app.store.close_alert(alert_id)
        removed = app.store.purge_expired(app.settings.retention_days)
        assert removed["metrics"] == 1
        assert removed["alerts"] == 1
        assert app.store.list_metrics("m1") == []

    def test_purge_keeps_open_alerts(self, app):
        old = time.time() - 30 * 86400
        alert_id = app.store.open_alert("m1", "10.0.0.1", "syn_flood", "high", 0.9, {}, [])
        app.store._execute("UPDATE alerts SET started_at = ? WHERE id = ?", (old, alert_id))
        app.store.purge_expired(app.settings.retention_days)
        assert app.store.list_alerts(monitor_id="m1", active_only=True)
