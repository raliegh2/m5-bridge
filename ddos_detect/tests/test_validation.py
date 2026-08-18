import ipaddress

import pytest

from ddos_detect.errors import ValidationError
from ddos_detect.validation import (
    address_scope,
    build_capture_filter,
    is_monitorable,
    network_scope,
    parse_ip,
    parse_network,
    validate_bool,
    validate_identifier,
    validate_int,
    validate_protocols,
    validate_text,
    validate_username,
)


class TestParseIp:
    @pytest.mark.parametrize("value", ["192.0.2.1", "10.0.0.1", "::1", "2001:db8::1"])
    def test_accepts_valid_addresses(self, value):
        assert str(parse_ip(value)) == str(ipaddress.ip_address(value))

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "999.1.1.1",
            "192.0.2",          # short form some resolvers would expand
            "0x7f.0.0.1",       # hex form
            "192.0.2.1 ; rm -rf /",
            "192.0.2.1\nhost evil",
            "example.com",
            "192.0.2.1/24",
            None,
            12345,
        ],
    )
    def test_rejects_anything_else(self, value):
        with pytest.raises(ValidationError):
            parse_ip(value)

    def test_rejects_overlong_input(self):
        with pytest.raises(ValidationError):
            parse_ip("1" * 200)


class TestScope:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("127.0.0.1", "loopback"),
            ("10.1.2.3", "private"),
            ("192.168.5.5", "private"),
            ("169.254.1.1", "link-local"),
            ("224.0.0.1", "multicast"),
            ("8.8.8.8", "public"),
            ("::1", "loopback"),
        ],
    )
    def test_address_scope(self, value, expected):
        assert address_scope(parse_ip(value)) == expected

    def test_network_scope_flags_public_ranges(self):
        assert network_scope(parse_network("8.8.8.0/24")) == "public"
        assert network_scope(parse_network("10.0.0.0/8")) == "private"

    def test_multicast_and_unspecified_are_not_monitorable(self):
        assert not is_monitorable(parse_ip("224.0.0.1"))
        assert not is_monitorable(parse_ip("0.0.0.0"))
        assert is_monitorable(parse_ip("192.0.2.1"))


class TestCaptureFilter:
    def test_renders_all_protocols_as_bare_host(self):
        assert build_capture_filter(parse_ip("192.0.2.1"), ["tcp", "udp", "icmp"]) == \
            "ip host 192.0.2.1"

    def test_renders_protocol_subset(self):
        assert build_capture_filter(parse_ip("192.0.2.1"), ["tcp"]) == \
            "ip host 192.0.2.1 and (tcp)"

    def test_uses_ipv6_tokens(self):
        result = build_capture_filter(parse_ip("2001:db8::1"), ["icmp"])
        assert result == "ip6 host 2001:db8::1 and (icmp6)"

    def test_rejects_injected_protocol(self):
        # The filter is a small language; caller text must never reach it.
        with pytest.raises(ValidationError):
            build_capture_filter(parse_ip("192.0.2.1"), ["tcp or not host 10.0.0.1"])

    def test_rejects_injected_target(self):
        with pytest.raises(ValidationError):
            build_capture_filter("192.0.2.1 or host 10.0.0.1", ["tcp"])

    def test_output_contains_only_canonical_address(self):
        # Whatever the input spelling, only ipaddress' canonical form appears.
        result = build_capture_filter(parse_ip("2001:0db8:0000::0001"), ["tcp", "udp", "icmp"])
        assert result == "ip6 host 2001:db8::1"


class TestScalars:
    def test_protocol_defaults_to_all(self):
        assert set(validate_protocols(None)) == {"tcp", "udp", "icmp"}

    def test_protocols_accept_csv(self):
        assert validate_protocols("tcp, udp") == ("tcp", "udp")

    def test_protocols_deduplicate(self):
        assert validate_protocols(["tcp", "tcp"]) == ("tcp",)

    @pytest.mark.parametrize("value", ["arp", ["sctp"], 5, "tcp;udp"])
    def test_protocols_reject_unknown(self, value):
        with pytest.raises(ValidationError):
            validate_protocols(value)

    def test_empty_selection_means_all_protocols(self):
        assert set(validate_protocols("")) == {"tcp", "udp", "icmp"}
        assert set(validate_protocols([])) == {"tcp", "udp", "icmp"}

    def test_text_rejects_control_characters(self):
        with pytest.raises(ValidationError):
            validate_text("line one\nlog injected", field="justification")

    def test_text_enforces_length(self):
        with pytest.raises(ValidationError):
            validate_text("x" * 501, field="justification")

    def test_int_range_is_inclusive(self):
        assert validate_int("5", field="n", minimum=1, maximum=5) == 5
        with pytest.raises(ValidationError):
            validate_int("6", field="n", minimum=1, maximum=5)

    def test_int_rejects_bool(self):
        with pytest.raises(ValidationError):
            validate_int(True, field="n", minimum=0, maximum=5)

    @pytest.mark.parametrize("value,expected", [("yes", True), ("off", False), (True, True)])
    def test_bool_forms(self, value, expected):
        assert validate_bool(value, field="flag") is expected

    def test_bool_rejects_nonsense(self):
        with pytest.raises(ValidationError):
            validate_bool("maybe", field="flag")

    @pytest.mark.parametrize("value", ["ab", "-leading", "has space", "x" * 40, "a/b"])
    def test_username_rules(self, value):
        with pytest.raises(ValidationError):
            validate_username(value)

    def test_username_normalises_case(self):
        assert validate_username("  Analyst  ") == "analyst"

    def test_identifier_rejects_path_traversal(self):
        with pytest.raises(ValidationError):
            validate_identifier("../../etc/passwd", field="monitor")
