"""Detection behaviour, driven entirely by generated traffic."""

from __future__ import annotations

import pytest

from ddos_detect.config import Thresholds
from ddos_detect.detector import (
    STATE_ATTACK,
    STATE_NORMAL,
    STATE_RECOVERING,
    STATE_SUSPECTED,
    Baseline,
    Detector,
    RollingWindow,
    ramp,
    shannon_entropy,
)
from ddos_detect.packets import ACK, PROTO_TCP, PROTO_UDP, SYN, PacketRecord
from ddos_detect.simulate import build_scenario


def run_scenario(name: str, **kwargs):
    """Replay a scenario second by second and return every evaluation."""
    scenario = build_scenario(name, **kwargs)
    detector = Detector(target=scenario.target, started_at=0.0)
    evaluations = []
    index = 0
    for second in range(int(scenario.duration)):
        while index < len(scenario.records) and scenario.records[index].ts < second + 1:
            detector.observe(scenario.records[index])
            index += 1
        evaluations.append(detector.evaluate(float(second) + 1.0))
    return scenario, evaluations


class TestHelpers:
    def test_ramp_clamps_both_ends(self):
        assert ramp(0, 10, 20) == 0.0
        assert ramp(15, 10, 20) == 0.5
        assert ramp(999, 10, 20) == 1.0

    def test_ramp_handles_degenerate_range(self):
        assert ramp(10, 10, 10) == 1.0
        assert ramp(9, 10, 10) == 0.0

    def test_entropy_is_zero_for_single_source(self):
        assert shannon_entropy([100]) == 0.0

    def test_entropy_is_one_for_uniform_sources(self):
        assert shannon_entropy([10, 10, 10, 10]) == pytest.approx(1.0)

    def test_entropy_is_low_when_one_source_dominates(self):
        assert shannon_entropy([1000, 1, 1, 1]) < 0.2


class TestRollingWindow:
    def test_rate_accounts_for_silent_seconds(self):
        # 100 packets in second 0, nothing after. Averaged over the elapsed
        # window the rate must fall, not stay at 100/s.
        window = RollingWindow(bucket_seconds=1, bucket_count=10, max_sources=1000)
        for i in range(100):
            window.observe(PacketRecord(0.5, f"10.0.0.{i % 50}", "10.1.1.1", PROTO_UDP, 100))
        assert window.totals(0.9)["pps"] == pytest.approx(100.0)
        assert window.totals(4.0)["pps"] == pytest.approx(20.0)

    def test_old_buckets_are_evicted(self):
        window = RollingWindow(bucket_seconds=1, bucket_count=5, max_sources=1000)
        for i in range(50):
            window.observe(PacketRecord(0.5, "10.0.0.1", "10.1.1.1", PROTO_UDP, 100))
        assert window.totals(0.9)["packets"] == 50
        assert window.totals(20.0)["packets"] == 0

    def test_source_tracking_is_capped(self):
        # A spoofed-source flood must not be able to exhaust memory through
        # the very structure meant to detect it.
        window = RollingWindow(bucket_seconds=1, bucket_count=10, max_sources=100)
        for i in range(5000):
            window.observe(PacketRecord(0.5, f"10.{i // 256}.{i % 256}.1", "10.1.1.1",
                                        PROTO_UDP, 100))
        totals = window.totals(0.9)
        assert totals["packets"] == 5000
        assert totals["source_overflow"] > 0
        # Tracking stops at the per-bucket cap even though 5000 distinct
        # sources were seen, and the overflow count records that it happened.
        assert len(window.buckets[0].sources) <= window.max_sources_per_bucket

    def test_outbound_packets_do_not_count_as_volume(self):
        window = RollingWindow(bucket_seconds=1, bucket_count=10, max_sources=100)
        for _ in range(10):
            window.observe(PacketRecord(0.5, "10.1.1.1", "10.0.0.2", PROTO_TCP, 1000,
                                        tcp_flags=SYN | ACK, inbound=False))
        totals = window.totals(0.9)
        assert totals["packets"] == 0
        assert totals["syn_ack_pps"] == pytest.approx(10.0)

    def test_top_dst_port_share(self):
        window = RollingWindow(bucket_seconds=1, bucket_count=10, max_sources=100)
        for i in range(90):
            window.observe(PacketRecord(0.5, "10.0.0.1", "10.1.1.1", PROTO_UDP, 100,
                                        dst_port=9999))
        for i in range(10):
            window.observe(PacketRecord(0.5, "10.0.0.1", "10.1.1.1", PROTO_UDP, 100,
                                        dst_port=443))
        assert window.totals(0.9)["top_dst_port_share"] == pytest.approx(0.9)


class TestBaseline:
    def test_no_zscore_before_enough_samples(self):
        baseline = Baseline()
        baseline.update(10)
        assert baseline.zscore(1000, floor=1.0) == 0.0

    def test_zscore_grows_with_deviation(self):
        baseline = Baseline()
        for _ in range(50):
            baseline.update(100.0)
        assert baseline.zscore(100.0, floor=1.0) == pytest.approx(0.0, abs=0.5)
        assert baseline.zscore(10_000.0, floor=1.0) > 10


class TestScenarios:
    """Each scenario asserts both detection and classification."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("syn_flood", "syn_flood"),
            ("udp_flood", "udp_flood"),
            ("icmp_flood", "icmp_flood"),
            ("amplification", "amplification"),
            ("botnet", "distributed_sources"),
        ],
    )
    def test_attacks_are_detected_and_classified(self, name, expected):
        scenario, evaluations = run_scenario(name)
        alerting = [e for e in evaluations if e.severity != "none"]
        assert alerting, f"{name} produced no alert"
        assert alerting[0].classification == expected
        # Nothing may fire before the attack begins.
        assert alerting[0].ts >= scenario.attack_start
        # Detection must be prompt, not eventual.
        assert alerting[0].ts - scenario.attack_start <= 10

    @pytest.mark.parametrize("name", ["baseline", "flash_crowd"])
    def test_legitimate_traffic_does_not_alert(self, name):
        _, evaluations = run_scenario(name)
        alerting = [e for e in evaluations if e.severity != "none"]
        assert alerting == [], (
            f"{name} produced {len(alerting)} false positives: "
            f"{alerting[0].as_dict() if alerting else ''}"
        )

    def test_flash_crowd_is_a_real_surge(self):
        # Guards the test above from passing trivially: the surge must be large
        # enough that a naive volume-only detector would have alerted on it.
        _, evaluations = run_scenario("flash_crowd")
        peak = max(e.metrics["pps"] for e in evaluations)
        quiet = min(e.metrics["pps"] for e in evaluations[20:80])
        assert peak > quiet * 5

    def test_spoofed_flood_is_marked_distributed(self):
        _, evaluations = run_scenario("syn_flood")
        alerting = [e for e in evaluations if e.severity != "none"]
        assert alerting[0].distributed is True
        assert alerting[0].label.startswith("Distributed")

    def test_amplification_is_not_marked_distributed(self):
        # Reflection comes from a handful of abused servers, not a botnet.
        _, evaluations = run_scenario("amplification")
        alerting = [e for e in evaluations if e.severity != "none"]
        assert alerting[0].distributed is False

    def test_alert_clears_after_attack_ends(self):
        scenario, evaluations = run_scenario("syn_flood")
        tail = [e for e in evaluations if e.ts > scenario.attack_end + 40]
        assert tail, "scenario is too short to observe recovery"
        assert all(e.severity == "none" for e in tail)
        assert tail[-1].state == STATE_NORMAL

    def test_every_alert_carries_actionable_guidance(self):
        for name in ("syn_flood", "udp_flood", "icmp_flood", "amplification", "botnet"):
            _, evaluations = run_scenario(name)
            alerting = [e for e in evaluations if e.severity != "none"]
            assert alerting[0].advice, f"{name} alert has no guidance"


class TestStateMachine:
    def _flood(self, detector: Detector, second: float, count: int = 3000) -> None:
        for i in range(count):
            detector.observe(PacketRecord(
                second + (i / count), f"10.{i // 256 % 256}.{i % 256}.7", "10.1.1.1",
                PROTO_TCP, 60, src_port=1024 + i % 60000, dst_port=443, tcp_flags=SYN,
            ))

    def test_single_spike_does_not_open_an_alert(self):
        # Hysteresis: one bad second is noise, not an incident.
        detector = Detector("10.1.1.1", learning_seconds=0.0, started_at=0.0)
        detector.evaluate(1.0)
        self._flood(detector, 1.0)
        evaluation = detector.evaluate(2.0)
        assert evaluation.state != STATE_ATTACK
        assert evaluation.severity == "none"

    def test_sustained_flood_escalates_to_attack(self):
        detector = Detector("10.1.1.1", learning_seconds=0.0, started_at=0.0)
        detector.evaluate(1.0)
        state = None
        for second in range(1, 8):
            self._flood(detector, float(second))
            state = detector.evaluate(float(second) + 1.0).state
        assert state == STATE_ATTACK

    def test_recovery_requires_the_cooldown_to_elapse(self):
        thresholds = Thresholds(cooldown_seconds=30.0)
        detector = Detector("10.1.1.1", thresholds=thresholds, learning_seconds=0.0,
                            started_at=0.0)
        for second in range(1, 8):
            self._flood(detector, float(second))
            detector.evaluate(float(second) + 1.0)
        assert detector.state == STATE_ATTACK
        # Traffic stops. The alert must not clear on the very next tick.
        assert detector.evaluate(20.0).state == STATE_RECOVERING
        assert detector.evaluate(30.0).state == STATE_RECOVERING
        assert detector.evaluate(80.0).state == STATE_NORMAL

    def test_baseline_does_not_learn_during_an_attack(self):
        # Otherwise a slow ramp would train the detector to accept the flood.
        detector = Detector("10.1.1.1", learning_seconds=0.0, started_at=0.0)
        for second in range(1, 10):
            self._flood(detector, float(second))
            detector.evaluate(float(second) + 1.0)
        assert detector.state in (STATE_ATTACK, STATE_SUSPECTED)
        assert detector.pps_baseline.mean < 500

    def test_learning_period_suppresses_alerts(self):
        detector = Detector("10.1.1.1", learning_seconds=60.0, started_at=0.0)
        for second in range(1, 10):
            self._flood(detector, float(second))
            evaluation = detector.evaluate(float(second) + 1.0)
            assert evaluation.severity == "none"
            assert evaluation.state == "learning"


class TestSourceFormatting:
    def test_top_sources_pass_through_the_formatter(self):
        detector = Detector("10.1.1.1", learning_seconds=0.0, started_at=0.0,
                            source_formatter=lambda src: "anon:" + src[-1])
        for i in range(20):
            detector.observe(PacketRecord(0.5, "10.0.0.3", "10.1.1.1", PROTO_UDP, 100))
        evaluation = detector.evaluate(1.0)
        assert evaluation.top_sources[0][0] == "anon:3"

    def test_evaluation_serialises_to_json_safe_types(self):
        import json

        _, evaluations = run_scenario("syn_flood")
        payload = [e.as_dict() for e in evaluations[:5]]
        json.dumps(payload)  # must not raise
