"""Header decoding, including the malformed-input cases.

A capture loop that can be crashed or misled by a crafted packet would be a
denial-of-service vector inside the denial-of-service detector, so the decoder
is expected to return ``None`` rather than raise on anything it cannot parse.
"""

from __future__ import annotations

import ipaddress
import struct

import pytest

from ddos_detect.decode import decode_frame, decode_ipv4, decode_ipv6, strip_link_layer
from ddos_detect.packets import PROTO_ICMP, PROTO_OTHER, PROTO_TCP, PROTO_UDP, SYN

ETH_IPV4 = bytes(6) + bytes(6) + struct.pack("!H", 0x0800)
ETH_IPV6 = bytes(6) + bytes(6) + struct.pack("!H", 0x86DD)


def ipv4(proto=6, total_length=60, src="192.0.2.1", dst="198.51.100.1", ttl=64,
         frag=0, payload=b"") -> bytes:
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total_length, 0x1234, frag, ttl, proto, 0,
        ipaddress.IPv4Address(src).packed, ipaddress.IPv4Address(dst).packed,
    )
    return header + payload


def tcp(src_port=12345, dst_port=443, flags=SYN) -> bytes:
    # ports, seq, ack, offset/reserved, flags, window
    return struct.pack("!HHIIBBH", src_port, dst_port, 1, 0, 0x50, flags, 8192)


def udp(src_port=53, dst_port=40000, length=100) -> bytes:
    return struct.pack("!HHHH", src_port, dst_port, length, 0)


class TestIPv4:
    def test_decodes_tcp(self):
        record = decode_ipv4(ipv4(proto=6, payload=tcp()), ts=1.0)
        assert record.protocol == PROTO_TCP
        assert record.src_ip == "192.0.2.1"
        assert record.dst_ip == "198.51.100.1"
        assert record.src_port == 12345
        assert record.dst_port == 443
        assert record.is_syn

    def test_decodes_udp(self):
        record = decode_ipv4(ipv4(proto=17, payload=udp()), ts=1.0)
        assert record.protocol == PROTO_UDP
        assert record.src_port == 53
        assert record.from_amplifier_port

    def test_decodes_icmp(self):
        record = decode_ipv4(ipv4(proto=1, payload=b"\x08\x00"), ts=1.0)
        assert record.protocol == PROTO_ICMP

    def test_unknown_protocol_is_other(self):
        assert decode_ipv4(ipv4(proto=89), ts=1.0).protocol == PROTO_OTHER

    def test_length_comes_from_the_header_not_the_buffer(self):
        # The capture buffer is truncated to the snaplen, so byte volume must
        # be read from the IP header or every rate would be understated.
        buf = ipv4(total_length=1500, payload=tcp())
        record = decode_ipv4(buf[:60], ts=1.0)
        assert record.length == 1500
        assert len(buf[:60]) < 1500

    def test_later_fragments_have_no_ports(self):
        record = decode_ipv4(ipv4(proto=6, frag=0x0025, payload=tcp()), ts=1.0)
        assert record.src_port == 0
        assert record.dst_port == 0

    @pytest.mark.parametrize("buf", [
        b"", b"\x45", b"\x45\x00", bytes(19),
        b"\x55" + bytes(30),          # wrong version nibble
        b"\x40" + bytes(30),          # IHL of zero
    ])
    def test_malformed_input_returns_none(self, buf):
        assert decode_ipv4(buf, ts=1.0) is None

    def test_truncated_transport_header_still_yields_a_record(self):
        record = decode_ipv4(ipv4(proto=6, payload=b"\x30\x39"), ts=1.0)
        assert record is not None
        assert record.protocol == PROTO_TCP
        assert record.src_port == 0  # not enough bytes to trust the ports


class TestIPv6:
    def test_decodes_tcp(self):
        header = struct.pack(
            "!IHBB", 0x60000000, 20, 6, 64,
        ) + ipaddress.IPv6Address("2001:db8::1").packed \
          + ipaddress.IPv6Address("2001:db8::2").packed
        record = decode_ipv6(header + tcp(), ts=1.0)
        assert record.protocol == PROTO_TCP
        assert record.src_ip == "2001:db8::1"
        assert record.length == 60

    @pytest.mark.parametrize("buf", [b"", bytes(39), b"\x40" + bytes(50)])
    def test_malformed_input_returns_none(self, buf):
        assert decode_ipv6(buf, ts=1.0) is None


class TestLinkLayer:
    def test_strips_ethernet(self):
        ethertype, payload = strip_link_layer(ETH_IPV4 + b"payload")
        assert ethertype == 0x0800
        assert payload == b"payload"

    def test_strips_a_vlan_tag(self):
        frame = bytes(12) + struct.pack("!H", 0x8100) + struct.pack("!HH", 0, 0x0800) + b"x"
        ethertype, payload = strip_link_layer(frame)
        assert ethertype == 0x0800
        assert payload == b"x"

    def test_short_frame_returns_none(self):
        assert strip_link_layer(b"\x00" * 8) is None

    def test_decode_frame_handles_both_link_modes(self):
        packet = ipv4(proto=6, payload=tcp())
        assert decode_frame(ETH_IPV4 + packet, 1.0, has_link_layer=True) is not None
        assert decode_frame(packet, 1.0, has_link_layer=False) is not None

    def test_non_ip_ethertype_is_ignored(self):
        arp = bytes(12) + struct.pack("!H", 0x0806) + bytes(28)
        assert decode_frame(arp, 1.0, has_link_layer=True) is None

    @pytest.mark.parametrize("buf", [b"", b"\x00", bytes(3), b"\xff" * 200])
    def test_garbage_never_raises(self, buf):
        # Whatever arrives on the wire, decoding must not throw.
        decode_frame(buf, 1.0, has_link_layer=True)
        decode_frame(buf, 1.0, has_link_layer=False)
