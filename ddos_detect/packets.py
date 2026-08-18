"""The packet record that the whole system is built around.

A :class:`PacketRecord` carries header-derived metadata only. There is
deliberately no payload field: the capture backends never read past the
transport header, so payload bytes cannot reach storage, the dashboard, or a
log even by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

# TCP flag bits, used to distinguish half-open floods from normal handshakes.
FIN = 0x01
SYN = 0x02
RST = 0x04
PSH = 0x08
ACK = 0x10
URG = 0x20

PROTO_TCP = "tcp"
PROTO_UDP = "udp"
PROTO_ICMP = "icmp"
PROTO_OTHER = "other"

#: Source ports characteristic of reflection/amplification responses. Seeing a
#: large share of inbound traffic *sourced* from these ports, with large mean
#: packet sizes, is the signature of a reflected amplification attack.
AMPLIFIER_PORTS = frozenset(
    {
        17,     # QOTD
        19,     # CharGen
        53,     # DNS
        69,     # TFTP
        123,    # NTP
        137,    # NetBIOS
        161,    # SNMP
        389,    # CLDAP
        520,    # RIP
        623,    # IPMI
        1194,   # OpenVPN
        1900,   # SSDP
        3283,   # Apple Remote Desktop
        3702,   # WS-Discovery
        5093,   # Sentinel
        5351,   # NAT-PMP
        5353,   # mDNS
        11211,  # memcached
        27015,  # Source engine
        33848,  # Jenkins
    }
)


@dataclass(frozen=True, slots=True)
class PacketRecord:
    """One observed packet, reduced to the fields detection needs."""

    ts: float
    src_ip: str
    dst_ip: str
    protocol: str
    length: int
    src_port: int = 0
    dst_port: int = 0
    tcp_flags: int = 0
    ttl: int = 0
    #: True when the packet travels *toward* the monitored target. Rates are
    #: derived from inbound traffic only, so the target's own replies cannot
    #: inflate the volume it is being judged on; outbound packets are still
    #: observed, because SYN-ACKs are what prove handshakes are completing.
    inbound: bool = True

    @property
    def is_syn(self) -> bool:
        """A connection request: SYN set, ACK clear."""
        return bool(self.tcp_flags & SYN) and not self.tcp_flags & ACK

    @property
    def is_syn_ack(self) -> bool:
        return bool(self.tcp_flags & SYN) and bool(self.tcp_flags & ACK)

    @property
    def is_ack(self) -> bool:
        return bool(self.tcp_flags & ACK) and not self.tcp_flags & SYN

    @property
    def is_rst(self) -> bool:
        return bool(self.tcp_flags & RST)

    @property
    def from_amplifier_port(self) -> bool:
        return self.protocol == PROTO_UDP and self.src_port in AMPLIFIER_PORTS
