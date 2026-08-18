"""Detection engine.

The engine is deliberately pure: it takes :class:`~ddos_detect.packets.PacketRecord`
objects and a clock, and returns :class:`Evaluation` objects. It performs no
I/O, so every rule below is unit-testable with synthetic traffic.

How a verdict is reached
------------------------
1. Packets land in one-second buckets held in a rolling window
   (:class:`RollingWindow`), which also maintains a window-wide source counter.
2. Each evaluation derives rate metrics from the window.
3. Independent rules turn those metrics into :class:`Signal` objects scored
   0-1. Rules combine an *absolute floor* (is this actually enough traffic to
   deny service?) with a *shape test* (does the traffic look like an attack
   rather than a busy hour?), so a popular site's traffic spike does not
   register as a flood on its own.
4. An EWMA baseline supplies z-scores for volumetric anomalies. The baseline
   only learns while the target is in a normal state, so a sustained attack
   cannot quietly become the new normal.
5. A hysteresis state machine converts scores into alerts, requiring several
   consecutive breaches to open and a cooldown to close, which keeps a single
   noisy second from producing an alert.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .config import Thresholds
from .packets import PROTO_ICMP, PROTO_TCP, PROTO_UDP, PacketRecord

STATE_LEARNING = "learning"
STATE_NORMAL = "normal"
STATE_SUSPECTED = "suspected"
STATE_ATTACK = "attack"
STATE_RECOVERING = "recovering"

SEVERITY_NONE = "none"
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"

TRANSITION_NONE = "none"
TRANSITION_OPENED = "opened"
TRANSITION_ESCALATED = "escalated"
TRANSITION_UPDATED = "updated"
TRANSITION_CLOSED = "closed"

#: Human-readable name and defensive guidance per rule. Guidance is advisory
#: text only - this system never takes network action on the operator's behalf.
CLASSIFICATIONS: dict[str, dict[str, str]] = {
    "syn_flood": {
        "label": "TCP SYN flood",
        "advice": "Enable SYN cookies, shorten the half-open timeout, and ask the upstream "
                  "provider to filter. Sources are usually spoofed, so blocking by address "
                  "has limited effect.",
    },
    "udp_flood": {
        "label": "UDP flood",
        "advice": "Rate-limit UDP toward the destination port at the edge and confirm with "
                  "the upstream provider whether the volume can be dropped before your link.",
    },
    "icmp_flood": {
        "label": "ICMP flood",
        "advice": "Rate-limit ICMP at the border router. Keep echo-reply available for "
                  "diagnostics rather than dropping ICMP wholesale.",
    },
    "amplification": {
        "label": "Reflected amplification",
        "advice": "Filter the reflected service port inbound, and report the abused "
                  "reflectors. Amplification cannot be stopped at the victim alone - "
                  "engage the upstream provider.",
    },
    "volumetric_anomaly": {
        "label": "Volumetric anomaly",
        "advice": "Bandwidth is far above the learned baseline. Check whether the link is "
                  "saturated and compare against a known-good traffic profile.",
    },
    "rate_anomaly": {
        "label": "Packet-rate anomaly",
        "advice": "Packet rate is far above baseline, which exhausts forwarding capacity "
                  "before bandwidth. Check for small-packet floods.",
    },
    "distributed_sources": {
        "label": "Distributed source spike",
        "advice": "Many distinct sources appeared at once, consistent with a botnet or "
                  "spoofed addressing. Per-source rate limits will not help; work upstream.",
    },
    "none": {"label": "No attack detected", "advice": ""},
}


def ramp(value: float, start: float, full: float) -> float:
    """Map ``value`` onto 0-1, reaching 0 at ``start`` and 1 at ``full``."""
    if full <= start:
        return 1.0 if value >= full else 0.0
    if value <= start:
        return 0.0
    return min(1.0, (value - start) / (full - start))


def shannon_entropy(counts: Iterable[int]) -> float:
    """Normalised Shannon entropy (0-1) of a distribution.

    1.0 means traffic is spread evenly across sources (spoofed or botnet-like);
    values near 0 mean a single source dominates.
    """
    values = [c for c in counts if c > 0]
    if len(values) < 2:
        return 0.0
    total = float(sum(values))
    entropy = -sum((c / total) * math.log2(c / total) for c in values)
    return min(1.0, entropy / math.log2(len(values)))


@dataclass
class Bucket:
    """Aggregated counters for one time slice."""

    start: float
    packets: int = 0
    bytes: int = 0
    tcp: int = 0
    udp: int = 0
    icmp: int = 0
    syn: int = 0
    syn_ack: int = 0
    ack: int = 0
    rst: int = 0
    amplifier: int = 0
    amplifier_bytes: int = 0
    sources: Counter = field(default_factory=Counter)
    dst_ports: Counter = field(default_factory=Counter)
    source_overflow: int = 0


#: Rules describing *what kind* of attack this is. The classification reported
#: to the operator is always drawn from these.
TYPE_SIGNALS = (
    "syn_flood", "udp_flood", "icmp_flood", "amplification",
    "volumetric_anomaly", "rate_anomaly",
)
#: Rules describing a *property* of the attack rather than its type. A source
#: spike qualifies a flood as distributed; on its own it is not a flood type.
MODIFIER_SIGNALS = ("distributed_sources",)

#: Tie-break order when two rules score equally: a specific flood type outranks
#: a generic statistical anomaly, because it tells the operator more.
SPECIFICITY = {
    "syn_flood": 5, "amplification": 5, "udp_flood": 4, "icmp_flood": 4,
    "volumetric_anomaly": 2, "rate_anomaly": 2, "distributed_sources": 1,
}


@dataclass(frozen=True)
class Signal:
    """One rule's verdict."""

    name: str
    score: float
    detail: dict

    @property
    def label(self) -> str:
        return CLASSIFICATIONS.get(self.name, {}).get("label", self.name)

    @property
    def is_type(self) -> bool:
        return self.name in TYPE_SIGNALS


@dataclass(frozen=True)
class Evaluation:
    """The engine's output for a single point in time."""

    ts: float
    state: str
    previous_state: str
    score: float
    severity: str
    classification: str
    metrics: dict
    signals: tuple[Signal, ...]
    top_sources: tuple[tuple[str, int], ...]
    transition: str
    #: Set when the flood is coming from very many sources at once. It
    #: qualifies the classification ("Distributed TCP SYN flood") rather than
    #: being a type of its own.
    distributed: bool = False

    @property
    def label(self) -> str:
        base = CLASSIFICATIONS.get(self.classification, {}).get("label", self.classification)
        if self.distributed and self.classification not in ("none", "distributed_sources"):
            # Lower the leading capital only on ordinary words - "TCP" must
            # survive intact.
            head = base if base[:2].isupper() else base[0].lower() + base[1:]
            return "Distributed " + head
        return base

    @property
    def advice(self) -> str:
        return CLASSIFICATIONS.get(self.classification, {}).get("advice", "")

    def as_dict(self) -> dict:
        return {
            "ts": self.ts,
            "state": self.state,
            "previous_state": self.previous_state,
            "score": round(self.score, 4),
            "severity": self.severity,
            "classification": self.classification,
            "label": self.label,
            "distributed": self.distributed,
            "advice": self.advice,
            "metrics": {k: round(v, 4) if isinstance(v, float) else v
                        for k, v in self.metrics.items()},
            "signals": [
                {"name": s.name, "label": s.label, "score": round(s.score, 4), "detail": s.detail}
                for s in self.signals
            ],
            "top_sources": [{"source": src, "packets": count} for src, count in self.top_sources],
            "transition": self.transition,
        }


class RollingWindow:
    """Fixed-duration window of per-second buckets with window-wide totals.

    Source counting is capped: under a spoofed-source flood the number of
    distinct addresses is unbounded by design, so the structure being used to
    detect the attack must not be a memory-exhaustion vector itself. Once the
    cap is hit, further distinct sources are counted in aggregate, which still
    signals "very many sources" without retaining each one.
    """

    def __init__(self, bucket_seconds: int, bucket_count: int, max_sources: int) -> None:
        self.bucket_seconds = max(1, int(bucket_seconds))
        self.bucket_count = max(5, int(bucket_count))
        self.max_sources_per_bucket = max(64, int(max_sources) // self.bucket_count)
        self._buckets: deque[Bucket] = deque()
        self._sources: Counter = Counter()

    @property
    def buckets(self) -> Sequence[Bucket]:
        return tuple(self._buckets)

    def _bucket_start(self, ts: float) -> float:
        return math.floor(ts / self.bucket_seconds) * self.bucket_seconds

    def _bucket_for(self, ts: float) -> Bucket | None:
        start = self._bucket_start(ts)
        if self._buckets and start < self._buckets[0].start:
            return None  # Packet older than the window; drop it.
        if not self._buckets or start > self._buckets[-1].start:
            bucket = Bucket(start=start)
            self._buckets.append(bucket)
            self._trim(start)
            return bucket
        for bucket in reversed(self._buckets):
            if bucket.start == start:
                return bucket
        return None

    def _trim(self, now_start: float) -> None:
        horizon = now_start - self.bucket_seconds * (self.bucket_count - 1)
        while self._buckets and self._buckets[0].start < horizon:
            evicted = self._buckets.popleft()
            self._sources.subtract(evicted.sources)
        # Counter.subtract leaves zero/negative entries behind; clear them so
        # the window-wide counter cannot grow without bound over a long run.
        if len(self._sources) > self.max_sources_per_bucket * 4:
            self._sources = Counter({k: v for k, v in self._sources.items() if v > 0})

    def advance(self, now: float) -> None:
        """Roll the window forward to ``now`` without recording a packet."""
        self._trim(self._bucket_start(now))

    def observe(self, packet: PacketRecord) -> None:
        bucket = self._bucket_for(packet.ts)
        if bucket is None:
            return
        if not packet.inbound:
            # Outbound traffic contributes only handshake evidence. Counting it
            # as volume would let the target's own replies raise its threat score.
            if packet.protocol == PROTO_TCP and packet.is_syn_ack:
                bucket.syn_ack += 1
            return
        bucket.packets += 1
        bucket.bytes += max(0, int(packet.length))
        if packet.protocol == PROTO_TCP:
            bucket.tcp += 1
            if packet.is_syn:
                bucket.syn += 1
            elif packet.is_syn_ack:
                bucket.syn_ack += 1
            elif packet.is_ack:
                bucket.ack += 1
            if packet.is_rst:
                bucket.rst += 1
        elif packet.protocol == PROTO_UDP:
            bucket.udp += 1
            if packet.from_amplifier_port:
                bucket.amplifier += 1
                bucket.amplifier_bytes += max(0, int(packet.length))
        elif packet.protocol == PROTO_ICMP:
            bucket.icmp += 1

        if packet.dst_port and (packet.dst_port in bucket.dst_ports
                                or len(bucket.dst_ports) < 4096):
            bucket.dst_ports[packet.dst_port] += 1

        if packet.src_ip in bucket.sources or len(bucket.sources) < self.max_sources_per_bucket:
            bucket.sources[packet.src_ip] += 1
            self._sources[packet.src_ip] += 1
        else:
            bucket.source_overflow += 1

    def totals(self, now: float) -> dict:
        """Summarise the window as rates over its actual observed span."""
        self.advance(now)
        buckets = self._buckets
        if not buckets:
            return _empty_totals()
        # Span covers every slot from the oldest retained bucket up to now,
        # including silent ones. Counting only non-empty buckets would divide
        # by too small a number and turn intermittent traffic into a fake spike.
        first = buckets[0].start
        last_slot = max(self._bucket_start(now), buckets[-1].start)
        slots = int((last_slot - first) / self.bucket_seconds) + 1
        span = float(max(1, min(slots, self.bucket_count)) * self.bucket_seconds)
        agg = {
            "packets": sum(b.packets for b in buckets),
            "bytes": sum(b.bytes for b in buckets),
            "tcp": sum(b.tcp for b in buckets),
            "udp": sum(b.udp for b in buckets),
            "icmp": sum(b.icmp for b in buckets),
            "syn": sum(b.syn for b in buckets),
            "syn_ack": sum(b.syn_ack for b in buckets),
            "ack": sum(b.ack for b in buckets),
            "rst": sum(b.rst for b in buckets),
            "amplifier": sum(b.amplifier for b in buckets),
            "amplifier_bytes": sum(b.amplifier_bytes for b in buckets),
            "overflow": sum(b.source_overflow for b in buckets),
        }
        tracked = {src: count for src, count in self._sources.items() if count > 0}
        busiest = max(tracked.values(), default=0)
        dst_ports: Counter = Counter()
        for bucket in buckets:
            dst_ports.update(bucket.dst_ports)
        busiest_port = max(dst_ports.values(), default=0)
        unique_sources = len(tracked) + (1 if agg["overflow"] else 0)
        return {
            "span": float(span),
            "pps": agg["packets"] / span,
            "bps": agg["bytes"] * 8.0 / span,
            "syn_pps": agg["syn"] / span,
            "syn_ack_pps": agg["syn_ack"] / span,
            "ack_pps": agg["ack"] / span,
            "rst_pps": agg["rst"] / span,
            "udp_pps": agg["udp"] / span,
            "icmp_pps": agg["icmp"] / span,
            "tcp_pps": agg["tcp"] / span,
            "amplifier_pps": agg["amplifier"] / span,
            "amplifier_share": (agg["amplifier"] / agg["packets"]) if agg["packets"] else 0.0,
            "amplifier_mean_size": (
                agg["amplifier_bytes"] / agg["amplifier"] if agg["amplifier"] else 0.0
            ),
            "mean_packet_size": (agg["bytes"] / agg["packets"]) if agg["packets"] else 0.0,
            "unique_sources": unique_sources,
            "unique_sources_per_second": unique_sources / span,
            "source_overflow": agg["overflow"],
            #: Share of traffic from the single busiest source. High means one
            #: host is doing the flooding; low with many sources means it is
            #: distributed. Used to tell an attack from a legitimate surge.
            "top_source_share": (busiest / agg["packets"]) if agg["packets"] else 0.0,
            "distinct_dst_ports": len(dst_ports),
            #: Share of traffic aimed at the single busiest destination port.
            #: Counting distinct ports instead would be defeated by ordinary
            #: background traffic to other services on the same host.
            "top_dst_port_share": (busiest_port / agg["packets"]) if agg["packets"] else 0.0,
            "entropy": shannon_entropy(tracked.values()),
            "packets": agg["packets"],
            "bytes": agg["bytes"],
            "_sources": tracked,
        }


def _empty_totals() -> dict:
    return {
        "span": 1.0, "pps": 0.0, "bps": 0.0, "syn_pps": 0.0, "syn_ack_pps": 0.0,
        "ack_pps": 0.0, "rst_pps": 0.0, "udp_pps": 0.0, "icmp_pps": 0.0, "tcp_pps": 0.0,
        "amplifier_pps": 0.0, "amplifier_share": 0.0, "amplifier_mean_size": 0.0,
        "mean_packet_size": 0.0, "unique_sources": 0, "unique_sources_per_second": 0.0,
        "source_overflow": 0, "top_source_share": 0.0, "distinct_dst_ports": 0,
        "top_dst_port_share": 0.0, "entropy": 0.0,
        "packets": 0, "bytes": 0, "_sources": {},
    }


class Baseline:
    """Exponentially weighted mean and variance used for z-scores."""

    def __init__(self, alpha: float = 0.05) -> None:
        self.alpha = alpha
        self.mean = 0.0
        self.variance = 0.0
        self.samples = 0

    def update(self, value: float) -> None:
        if self.samples == 0:
            self.mean = value
            self.variance = 0.0
        else:
            delta = value - self.mean
            self.mean += self.alpha * delta
            self.variance = (1 - self.alpha) * (self.variance + self.alpha * delta * delta)
        self.samples += 1

    def zscore(self, value: float, floor: float) -> float:
        """Z-score with a variance floor, so a flat baseline is not infinitely sensitive."""
        if self.samples < 5:
            return 0.0
        std = max(math.sqrt(max(self.variance, 0.0)), floor)
        return (value - self.mean) / std


#: A specific rule may score slightly below a generic one and still be the
#: better description. Any type signal within this fraction of the strongest
#: is eligible to be the headline, and the most specific of those wins.
SPECIFICITY_MARGIN = 0.7


def _headline(signals: Sequence[Signal]) -> Signal:
    """Choose the single finding to lead with.

    Preference order, because the point is to tell the operator something they
    can act on:

    1. A specific flood type (SYN, UDP, ICMP, amplification). "ICMP flood"
       implies a response; "packet-rate anomaly" does not, so a specific rule
       wins even when a generic one scores slightly higher.
    2. Otherwise, a source-spike modifier if it is at least as strong as the
       generic anomalies - "traffic from 2,000 sources at once" is the real
       finding when nothing more specific matched.
    3. Otherwise the strongest generic anomaly.
    """
    types = [s for s in signals if s.is_type]
    specific = [s for s in types if SPECIFICITY.get(s.name, 0) >= 4]
    if specific:
        best = max(s.score for s in specific)
        contenders = [s for s in specific if s.score >= best * SPECIFICITY_MARGIN]
        return max(contenders, key=lambda s: (SPECIFICITY.get(s.name, 0), s.score))

    modifiers = [s for s in signals if not s.is_type]
    generic_best = max((s.score for s in types), default=0.0)
    if modifiers:
        strongest = max(modifiers, key=lambda s: s.score)
        # Same margin as above: "traffic from very many sources" is a more
        # useful headline than "packet-rate anomaly" even when it scores a
        # little lower, which it does early in an attack while the window fills.
        if strongest.score >= generic_best * SPECIFICITY_MARGIN:
            return strongest
    if types:
        return max(types, key=lambda s: s.score)
    return signals[0]


def severity_for(score: float, thresholds: Thresholds) -> str:
    if score < thresholds.score_warn:
        return SEVERITY_NONE
    if score < (thresholds.score_warn + thresholds.score_crit) / 2:
        return SEVERITY_LOW
    if score < thresholds.score_crit:
        return SEVERITY_MEDIUM
    if score < 0.9:
        return SEVERITY_HIGH
    return SEVERITY_CRITICAL


class Detector:
    """Per-target detection state machine."""

    def __init__(self, target: str, thresholds: Thresholds | None = None, *,
                 bucket_seconds: int = 1, bucket_count: int = 10,
                 learning_seconds: float = 60.0, max_sources: int = 20_000,
                 top_talkers: int = 10, started_at: float | None = None,
                 source_formatter: Callable[[str], str] | None = None) -> None:
        self.target = target
        self.thresholds = thresholds or Thresholds()
        self.window = RollingWindow(bucket_seconds, bucket_count, max_sources)
        self.learning_seconds = float(learning_seconds)
        self.top_talkers = int(top_talkers)
        self.started_at = started_at
        self._format_source = source_formatter or (lambda value: value)
        self.pps_baseline = Baseline()
        self.bps_baseline = Baseline()
        self.sources_baseline = Baseline()
        self.state = STATE_LEARNING
        self._warn_hits = 0
        self._crit_hits = 0
        self._clear_hits = 0
        self._below_since: float | None = None
        self._packets_seen = 0

    # -- ingestion -------------------------------------------------------
    def observe(self, packet: PacketRecord) -> None:
        if self.started_at is None:
            self.started_at = packet.ts
        self.window.observe(packet)
        self._packets_seen += 1

    def observe_many(self, packets: Iterable[PacketRecord]) -> None:
        for packet in packets:
            self.observe(packet)

    # -- rules -----------------------------------------------------------
    def _corroboration(self, m: dict) -> tuple[float, dict]:
        """How much does the traffic *shape* look like an attack? Returns 0-1.

        A flash crowd and a flood both show up as a volume spike. What
        separates them is shape, and an attack shows at least one of:

        * handshakes stop completing (half-open flood),
        * one source is responsible for most of the traffic (single-source flood),
        * a great many sources appear at once (botnet or spoofing).

        Legitimate surges show none of these: connections complete, and load is
        spread over an ordinary number of real clients.
        """
        t = self.thresholds
        completions = max(m["syn_ack_pps"] + m["ack_pps"], 1e-6)
        imbalance = ramp(m["syn_pps"] / completions, t.syn_ack_ratio, t.syn_ack_ratio * 4)
        concentration = ramp(m["top_source_share"], 0.35, 0.8)
        spread = ramp(m["unique_sources_per_second"], t.distributed_sources,
                      t.distributed_sources * 10)
        # Non-TCP traffic has no handshake to complete, so a UDP/ICMP-dominated
        # spike is judged on source shape alone rather than being excused.
        best = max(imbalance, concentration, spread)
        return best, {
            "corroboration": round(best, 3),
            "handshake_imbalance": round(imbalance, 3),
            "source_concentration": round(concentration, 3),
            "source_spread": round(spread, 3),
        }

    def _signals(self, m: dict) -> list[Signal]:
        t = self.thresholds
        signals: list[Signal] = []

        # 1. SYN flood: volume of connection requests that are not completing.
        # A busy web server also sends many SYNs, but its handshakes complete,
        # so the imbalance test is what separates load from attack.
        completions = max(m["syn_ack_pps"] + m["ack_pps"], 1e-6)
        syn_ratio = m["syn_pps"] / completions
        if m["syn_pps"] >= t.syn_pps and syn_ratio >= t.syn_ack_ratio:
            magnitude = ramp(m["syn_pps"], t.syn_pps, t.syn_pps * 10)
            imbalance = ramp(syn_ratio, t.syn_ack_ratio, t.syn_ack_ratio * 4)
            signals.append(Signal("syn_flood", min(1.0, 0.55 * magnitude + 0.45 * imbalance), {
                "syn_pps": round(m["syn_pps"], 2),
                "handshake_completions_pps": round(completions, 2),
                "syn_to_completion_ratio": round(syn_ratio, 2),
            }))

        # 2. UDP flood: high datagram rate, made more convincing when most of
        # it is aimed at one destination port. Real UDP services spread across
        # several ports, so concentration is the shape test here.
        if m["udp_pps"] >= t.udp_pps:
            magnitude = ramp(m["udp_pps"], t.udp_pps, t.udp_pps * 5)
            concentration = ramp(m["top_dst_port_share"], 0.4, 0.9)
            signals.append(Signal("udp_flood", min(1.0, 0.6 * magnitude + 0.4 * concentration), {
                "udp_pps": round(m["udp_pps"], 2),
                "top_dst_port_share": round(m["top_dst_port_share"], 3),
                "distinct_dst_ports": m["distinct_dst_ports"],
                "mean_packet_size": round(m["mean_packet_size"], 1),
            }))

        # 3. ICMP flood. ICMP carries no ports, so rate is the whole test.
        if m["icmp_pps"] >= t.icmp_pps:
            signals.append(Signal(
                "icmp_flood", ramp(m["icmp_pps"], t.icmp_pps, t.icmp_pps * 5),
                {"icmp_pps": round(m["icmp_pps"], 2)},
            ))

        # 4. Reflected amplification: traffic sourced from amplifier service
        # ports, with the large responses characteristic of amplification.
        if (m["amplifier_share"] >= t.amplification_share
                and m["amplifier_mean_size"] >= t.amplification_size
                and m["pps"] >= t.pps_floor):
            share = ramp(m["amplifier_share"], t.amplification_share, 0.95)
            size = ramp(m["amplifier_mean_size"], t.amplification_size, t.amplification_size * 4)
            signals.append(Signal("amplification", min(1.0, 0.5 * share + 0.5 * size), {
                "amplifier_share": round(m["amplifier_share"], 3),
                "amplifier_mean_size": round(m["amplifier_mean_size"], 1),
                "amplifier_pps": round(m["amplifier_pps"], 2),
            }))

        # 5/6. Statistical anomalies against the learned baseline. Two gates
        # apply. An absolute floor stops a quiet host alerting on a small
        # absolute change that happens to be a large relative one. Then the
        # score is scaled by corroborating *shape* - a legitimate traffic
        # surge is also a statistical anomaly, and volume alone cannot tell
        # the two apart, so an anomaly with no attack shape behind it is
        # reported weakly rather than treated as an attack.
        corroboration, corroboration_detail = self._corroboration(m)
        if m["bps"] >= t.bps_floor:
            z = self.bps_baseline.zscore(m["bps"], floor=max(t.bps_floor * 0.05, 1.0))
            if z >= t.zscore_warn:
                score = ramp(z, t.zscore_warn, t.zscore_crit * 1.5) * corroboration
                signals.append(Signal("volumetric_anomaly", score, {
                    "bps": round(m["bps"], 1), "baseline_bps": round(self.bps_baseline.mean, 1),
                    "zscore": round(z, 2), **corroboration_detail,
                }))
        if m["pps"] >= t.pps_floor:
            z = self.pps_baseline.zscore(m["pps"], floor=max(t.pps_floor * 0.05, 1.0))
            if z >= t.zscore_warn:
                score = ramp(z, t.zscore_warn, t.zscore_crit * 1.5) * corroboration
                signals.append(Signal("rate_anomaly", score, {
                    "pps": round(m["pps"], 1), "baseline_pps": round(self.pps_baseline.mean, 1),
                    "zscore": round(z, 2), **corroboration_detail,
                }))

        # 7. Distributed sources: many distinct addresses arriving at once with
        # traffic spread evenly between them - a botnet or spoofed addressing.
        # This is what distinguishes a *distributed* denial of service from a
        # single-source one, and it is reported alongside the flood type.
        sps = m["unique_sources_per_second"]
        if sps >= t.distributed_sources and m["pps"] >= t.pps_floor:
            spread = ramp(sps, t.distributed_sources, t.distributed_sources * 20)
            evenness = m["entropy"] if m["unique_sources"] > 8 else 0.0
            score = min(1.0, 0.6 * spread + 0.4 * evenness)
            if m["source_overflow"]:
                score = min(1.0, score + 0.2)  # source cap hit: even more sources than tracked
            signals.append(Signal("distributed_sources", score, {
                "unique_sources": m["unique_sources"],
                "unique_sources_per_second": round(sps, 2),
                "source_entropy": round(m["entropy"], 3),
                "source_tracking_capped": bool(m["source_overflow"]),
            }))

        # Drop rules whose corroboration reduced them to noise.
        signals = [s for s in signals if s.score > 0.01]
        signals.sort(key=lambda s: (s.score, SPECIFICITY.get(s.name, 0)), reverse=True)
        return signals

    @staticmethod
    def _aggregate(signals: Sequence[Signal]) -> float:
        """Combine signals: the strongest rule leads, corroboration adds a little.

        Corroboration is capped so that several weak signals can never
        manufacture a critical alert on their own.
        """
        if not signals:
            return 0.0
        primary = signals[0].score
        support = sum(s.score for s in signals[1:])
        return min(1.0, primary + min(0.2, 0.12 * support))

    # -- evaluation ------------------------------------------------------
    def evaluate(self, now: float) -> Evaluation:
        """Advance the state machine to ``now`` and return the verdict."""
        if self.started_at is None:
            self.started_at = now
        totals = self.window.totals(now)
        sources: dict[str, int] = totals.pop("_sources")
        previous = self.state

        learning = (now - self.started_at) < self.learning_seconds
        signals = tuple(self._signals(totals))
        score = self._aggregate(signals)
        t = self.thresholds

        if learning:
            self.state = STATE_LEARNING
            self._update_baselines(totals)
            score = 0.0
            signals = ()
        else:
            self._step_state(now, score)
            # Only learn from healthy traffic. Gating on the state alone is not
            # enough: the state machine deliberately waits several consecutive
            # breaches before leaving NORMAL, and the baseline would absorb the
            # opening seconds of the flood during exactly that window. Gating
            # on the score as well means nothing that already looks like an
            # attack can become the normal it is measured against.
            if self.state == STATE_NORMAL and score < t.score_warn:
                self._update_baselines(totals)

        severity = SEVERITY_NONE if self.state in (STATE_LEARNING, STATE_NORMAL) \
            else severity_for(score, t)
        # Report *what kind* of attack this is. A source spike qualifies a
        # flood rather than being one, so it is only the headline when no
        # flood type fired at all.
        classification = "none"
        distributed = False
        if signals and severity != SEVERITY_NONE:
            classification = _headline(signals).name
            distributed = any(
                s.name == "distributed_sources" and s.score >= 0.5 for s in signals
            )
        transition = self._transition(previous, self.state)

        top = tuple(
            (self._format_source(src), count)
            for src, count in sorted(sources.items(), key=lambda kv: kv[1], reverse=True)[
                : self.top_talkers
            ]
        )
        metrics = {k: v for k, v in totals.items() if not k.startswith("_")}
        metrics["baseline_pps"] = self.pps_baseline.mean
        metrics["baseline_bps"] = self.bps_baseline.mean
        metrics["packets_observed"] = self._packets_seen
        return Evaluation(
            ts=now, state=self.state, previous_state=previous, score=score, severity=severity,
            classification=classification, metrics=metrics, signals=signals,
            top_sources=top, transition=transition, distributed=distributed,
        )

    def _update_baselines(self, totals: dict) -> None:
        self.pps_baseline.update(totals["pps"])
        self.bps_baseline.update(totals["bps"])
        self.sources_baseline.update(totals["unique_sources_per_second"])

    def _step_state(self, now: float, score: float) -> None:
        t = self.thresholds
        if score >= t.score_crit:
            self._crit_hits += 1
            self._warn_hits += 1
            self._clear_hits = 0
            self._below_since = None
        elif score >= t.score_warn:
            self._crit_hits = 0
            self._warn_hits += 1
            self._clear_hits = 0
            self._below_since = None
        else:
            self._crit_hits = 0
            self._warn_hits = 0
            self._clear_hits += 1
            if self._below_since is None:
                self._below_since = now

        if self._crit_hits >= t.consecutive_hits:
            self.state = STATE_ATTACK
            return
        if self._warn_hits >= t.consecutive_hits:
            if self.state != STATE_ATTACK:
                self.state = STATE_SUSPECTED
            return
        if self.state in (STATE_SUSPECTED, STATE_ATTACK) and self._clear_hits > 0:
            self.state = STATE_RECOVERING
            return
        if self.state == STATE_RECOVERING:
            quiet_for = now - (self._below_since or now)
            if quiet_for >= t.cooldown_seconds:
                self.state = STATE_NORMAL
            return
        if self.state == STATE_LEARNING:
            self.state = STATE_NORMAL

    @staticmethod
    def _transition(previous: str, current: str) -> str:
        active = (STATE_SUSPECTED, STATE_ATTACK)
        if previous not in active and current in active:
            return TRANSITION_OPENED
        if previous == STATE_SUSPECTED and current == STATE_ATTACK:
            return TRANSITION_ESCALATED
        if previous in active and current in active:
            return TRANSITION_UPDATED
        if previous in (STATE_SUSPECTED, STATE_ATTACK, STATE_RECOVERING) and current == STATE_NORMAL:
            return TRANSITION_CLOSED
        if previous in active and current == STATE_RECOVERING:
            return TRANSITION_UPDATED
        return TRANSITION_NONE
