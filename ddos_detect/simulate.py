"""Synthetic traffic scenarios for testing and demonstration.

This module builds :class:`~ddos_detect.packets.PacketRecord` objects in
memory. It does not open a socket, does not send anything, and cannot be
pointed at a host: it is a fixture generator for the detector, not a traffic
generator. Running a scenario exercises the same code path a live capture
would drive, so detection logic can be validated without attacking anything.

Every scenario is seeded, so the same seed yields identical traffic and tests
are deterministic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from .packets import ACK, PROTO_ICMP, PROTO_TCP, PROTO_UDP, SYN, PacketRecord

SCENARIOS = (
    "baseline",
    "syn_flood",
    "udp_flood",
    "icmp_flood",
    "amplification",
    "botnet",
    "flash_crowd",
)

SCENARIO_HELP = {
    "baseline": "Ordinary mixed traffic with completing handshakes. Should not alert.",
    "syn_flood": "High-rate half-open TCP connections from spoofed sources.",
    "udp_flood": "High-rate UDP datagrams concentrated on one destination port.",
    "icmp_flood": "High-rate ICMP echo traffic.",
    "amplification": "Large DNS/NTP/memcached responses reflected off third parties.",
    "botnet": "Moderate rate from thousands of distinct sources - distributed, not spoofed.",
    "flash_crowd": "A legitimate traffic surge with healthy handshakes. Should NOT alert; "
                   "used to check the false-positive behaviour.",
}


@dataclass(frozen=True)
class Scenario:
    """A generated traffic timeline."""

    name: str
    target: str
    records: tuple[PacketRecord, ...]
    duration: float
    attack_start: float
    attack_end: float

    @property
    def packet_count(self) -> int:
        return len(self.records)


def _client_pool(rng: random.Random, size: int, prefix: str = "203.0.113") -> list[str]:
    """Addresses drawn from documentation ranges (RFC 5737 / RFC 3849)."""
    pool: set[str] = set()
    blocks = ("198.51.100", "203.0.113", "192.0.2")
    while len(pool) < size:
        block = rng.choice(blocks) if size > 250 else prefix
        pool.add(f"{block}.{rng.randint(1, 254)}")
        if len(pool) < size and size > 250:
            pool.add(f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}")
    return sorted(pool)


def _normal_second(rng: random.Random, target: str, second: float, clients: list[str],
                   rate: int) -> list[PacketRecord]:
    """One second of well-behaved traffic: handshakes that actually complete."""
    out: list[PacketRecord] = []
    for _ in range(rate):
        ts = second + rng.random()
        client = rng.choice(clients)
        port = rng.choice([80, 443, 443, 443, 8080])
        sport = rng.randint(1024, 65535)
        if rng.random() < 0.12:
            # New connection: SYN in, SYN-ACK back out, ACK in - a complete
            # handshake, which is exactly what a SYN flood lacks.
            out.append(PacketRecord(ts, client, target, PROTO_TCP, 60, sport, port, SYN, 64))
            out.append(PacketRecord(ts + 0.001, target, client, PROTO_TCP, 60, port, sport,
                                    SYN | ACK, 64, inbound=False))
            out.append(PacketRecord(ts + 0.002, client, target, PROTO_TCP, 52, sport, port,
                                    ACK, 64))
        elif rng.random() < 0.15:
            out.append(PacketRecord(ts, client, target, PROTO_UDP, rng.randint(64, 512),
                                    sport, 53, 0, 64))
        else:
            out.append(PacketRecord(ts, client, target, PROTO_TCP, rng.randint(200, 1400),
                                    sport, port, ACK | 0x08, 64))
    return out


def build_scenario(name: str, target: str = "192.0.2.10", *, duration: float = 180.0,
                   attack_start: float = 90.0, attack_duration: float = 45.0,
                   seed: int = 1337, baseline_rate: int = 40) -> Scenario:
    """Build a traffic timeline for ``name``.

    Timestamps start at 0 and are rebased onto the wall clock by
    :class:`~ddos_detect.capture.ReplayCapture`.
    """
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}")
    rng = random.Random(seed)
    attack_end = attack_start + attack_duration
    records: list[PacketRecord] = []
    attack_fn: Callable[[random.Random, str, float], list[PacketRecord]] | None = {
        "baseline": None,
        "syn_flood": _syn_flood_second,
        "udp_flood": _udp_flood_second,
        "icmp_flood": _icmp_flood_second,
        "amplification": _amplification_second,
        "botnet": _botnet_second,
        "flash_crowd": None,
    }[name]

    clients = _client_pool(rng, 40)
    for second in range(int(duration)):
        rate = baseline_rate
        if name == "flash_crowd" and attack_start <= second < attack_end:
            # A real surge: eight times the traffic, but handshakes still
            # complete and the source set stays realistic.
            rate = baseline_rate * 8
        records.extend(_normal_second(rng, target, float(second), clients, rate))
        if attack_fn is not None and attack_start <= second < attack_end:
            records.extend(attack_fn(rng, target, float(second)))

    records.sort(key=lambda r: r.ts)
    return Scenario(name, target, tuple(records), float(duration), attack_start, attack_end)


def _spoofed(rng: random.Random) -> str:
    return f"{rng.randint(11, 220)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def _syn_flood_second(rng: random.Random, target: str, second: float) -> list[PacketRecord]:
    # 4000 half-open connections per second from spoofed sources: no SYN-ACK
    # is ever answered, so the handshake-completion ratio collapses.
    return [
        PacketRecord(second + rng.random(), _spoofed(rng), target, PROTO_TCP, 60,
                     rng.randint(1024, 65535), 443, SYN, rng.randint(30, 64))
        for _ in range(4000)
    ]


def _udp_flood_second(rng: random.Random, target: str, second: float) -> list[PacketRecord]:
    return [
        PacketRecord(second + rng.random(), _spoofed(rng), target, PROTO_UDP,
                     rng.randint(800, 1400), rng.randint(1024, 65535), 9999, 0, 60)
        for _ in range(9000)
    ]


def _icmp_flood_second(rng: random.Random, target: str, second: float) -> list[PacketRecord]:
    return [
        PacketRecord(second + rng.random(), _spoofed(rng), target, PROTO_ICMP,
                     rng.randint(64, 1200), 0, 0, 0, 60)
        for _ in range(3000)
    ]


def _amplification_second(rng: random.Random, target: str, second: float) -> list[PacketRecord]:
    # Reflectors are real servers, so the source set is small and the responses
    # are large - the opposite shape to a spoofed SYN flood.
    reflectors = [f"198.18.{i}.{rng.randint(1, 254)}" for i in range(6)]
    ports = [53, 123, 11211, 1900]
    return [
        PacketRecord(second + rng.random(), rng.choice(reflectors), target, PROTO_UDP,
                     rng.randint(1200, 1480), rng.choice(ports), rng.randint(1024, 65535), 0, 55)
        for _ in range(3500)
    ]


def _botnet_second(rng: random.Random, target: str, second: float) -> list[PacketRecord]:
    # Real hosts, so each source sends only a little, but there are very many
    # of them and the traffic is spread evenly - high source entropy.
    return [
        PacketRecord(second + rng.random(), _spoofed(rng), target, PROTO_TCP,
                     rng.randint(300, 900), rng.randint(1024, 65535), 443, ACK | 0x08, 58)
        for _ in range(2500)
    ]
