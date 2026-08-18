"""Header decoding for the raw-socket capture backend.

Pure functions over :class:`bytes`, so they are unit-testable without any
privilege or network access. They read the link, network, and transport
headers and nothing beyond them: the caller only ever hands in a truncated
buffer, and these functions never index past the transport header regardless.

Every accessor is bounds-checked. A malformed or deliberately truncated packet
must return ``None``, never raise - a capture loop that can be crashed by a
crafted packet is a denial-of-service vector in the detector itself.
"""

from __future__ import annotations

import ipaddress
import socket
import struct

from .packets import PROTO_ICMP, PROTO_OTHER, PROTO_TCP, PROTO_UDP, PacketRecord

ETH_HEADER_LEN = 14
ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_IPV6 = 0x86DD
ETHERTYPE_VLAN = 0x8100
ETHERTYPE_QINQ = 0x88A8

IPPROTO_TCP = 6
IPPROTO_UDP = 17
IPPROTO_ICMP = 1
IPPROTO_ICMPV6 = 58

#: IPv6 extension headers that are skipped to reach the transport header.
_EXT_HEADERS = {0, 43, 44, 51, 60, 135}

_PROTO_NAMES = {
    IPPROTO_TCP: PROTO_TCP,
    IPPROTO_UDP: PROTO_UDP,
    IPPROTO_ICMP: PROTO_ICMP,
    IPPROTO_ICMPV6: PROTO_ICMP,
}


def strip_link_layer(buf: bytes) -> tuple[int, bytes] | None:
    """Strip Ethernet (and VLAN tags) and return ``(ethertype, payload)``."""
    if len(buf) < ETH_HEADER_LEN:
        return None
    ethertype = struct.unpack_from("!H", buf, 12)[0]
    offset = ETH_HEADER_LEN
    hops = 0
    while ethertype in (ETHERTYPE_VLAN, ETHERTYPE_QINQ) and hops < 2:
        if len(buf) < offset + 4:
            return None
        ethertype = struct.unpack_from("!H", buf, offset + 2)[0]
        offset += 4
        hops += 1
    return ethertype, buf[offset:]


def decode_ipv4(buf: bytes, ts: float) -> PacketRecord | None:
    """Decode an IPv4 packet from the start of ``buf``."""
    if len(buf) < 20:
        return None
    version_ihl = buf[0]
    if version_ihl >> 4 != 4:
        return None
    ihl = (version_ihl & 0x0F) * 4
    if ihl < 20:
        return None
    total_length, _ident, flags_frag, ttl, proto = struct.unpack_from("!HHHBB", buf, 2)
    src = str(ipaddress.IPv4Address(buf[12:16]))
    dst = str(ipaddress.IPv4Address(buf[16:20]))
    # Only the first fragment carries transport ports; later fragments are
    # counted for volume with zeroed ports rather than misparsed.
    is_first_fragment = (flags_frag & 0x1FFF) == 0
    payload = buf[ihl:]
    src_port, dst_port, tcp_flags = _decode_transport(proto, payload, is_first_fragment)
    return PacketRecord(
        ts=ts, src_ip=src, dst_ip=dst,
        protocol=_PROTO_NAMES.get(proto, PROTO_OTHER),
        # Trust the header's own length field: the buffer is truncated to the
        # snaplen, so len(buf) would systematically under-report byte volume.
        length=max(total_length, ihl),
        src_port=src_port, dst_port=dst_port, tcp_flags=tcp_flags, ttl=ttl,
    )


def decode_ipv6(buf: bytes, ts: float) -> PacketRecord | None:
    """Decode an IPv6 packet from the start of ``buf``."""
    if len(buf) < 40:
        return None
    first = buf[0]
    if first >> 4 != 6:
        return None
    payload_length, next_header, hop_limit = struct.unpack_from("!HBB", buf, 4)
    src = str(ipaddress.IPv6Address(buf[8:24]))
    dst = str(ipaddress.IPv6Address(buf[24:40]))
    offset = 40
    hops = 0
    while next_header in _EXT_HEADERS and hops < 4:
        if len(buf) < offset + 8:
            next_header = 0
            break
        ext_next = buf[offset]
        ext_len = (buf[offset + 1] + 1) * 8
        next_header = ext_next
        offset += ext_len
        hops += 1
    payload = buf[offset:] if offset <= len(buf) else b""
    src_port, dst_port, tcp_flags = _decode_transport(next_header, payload, True)
    return PacketRecord(
        ts=ts, src_ip=src, dst_ip=dst,
        protocol=_PROTO_NAMES.get(next_header, PROTO_OTHER),
        length=payload_length + 40,
        src_port=src_port, dst_port=dst_port, tcp_flags=tcp_flags, ttl=hop_limit,
    )


def _decode_transport(proto: int, payload: bytes, first_fragment: bool) -> tuple[int, int, int]:
    if not first_fragment:
        return 0, 0, 0
    if proto == IPPROTO_TCP and len(payload) >= 14:
        src_port, dst_port = struct.unpack_from("!HH", payload, 0)
        flags = payload[13]
        return src_port, dst_port, flags
    if proto == IPPROTO_UDP and len(payload) >= 4:
        src_port, dst_port = struct.unpack_from("!HH", payload, 0)
        return src_port, dst_port, 0
    return 0, 0, 0


def decode_frame(buf: bytes, ts: float, *, has_link_layer: bool) -> PacketRecord | None:
    """Decode one captured frame into a :class:`PacketRecord`, or ``None``.

    ``has_link_layer`` is True for AF_PACKET captures (Ethernet frames) and
    False for raw IP sockets, which deliver the IP header directly.
    """
    try:
        if has_link_layer:
            stripped = strip_link_layer(buf)
            if stripped is None:
                return None
            ethertype, payload = stripped
            if ethertype == ETHERTYPE_IPV4:
                return decode_ipv4(payload, ts)
            if ethertype == ETHERTYPE_IPV6:
                return decode_ipv6(payload, ts)
            return None
        if not buf:
            return None
        version = buf[0] >> 4
        if version == 4:
            return decode_ipv4(buf, ts)
        if version == 6:
            return decode_ipv6(buf, ts)
        return None
    except (struct.error, ValueError, IndexError):
        # A crafted packet must not be able to stop the capture loop.
        return None


def htons(value: int) -> int:
    return socket.htons(value)
