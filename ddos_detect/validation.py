"""Strict input validation.

Everything that originates outside the process - HTTP request bodies, query
strings, CLI arguments, environment variables - passes through this module
before it reaches storage, the capture backend, or the detection engine.

Two rules drive the design:

1. Parse, don't sanitise. Text is converted into a typed object
   (:class:`ipaddress.IPv4Address`, :class:`ipaddress.IPv4Network`, ``int``)
   and the typed object is what the rest of the system uses.
2. Never interpolate caller text into an interpreted language. The BPF filter
   handed to the capture backend is rendered from validated objects and a
   fixed vocabulary only - see :func:`build_capture_filter`.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable, Sequence

from .errors import ValidationError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Interface names across Linux/BSD/Windows-with-npcap. Deliberately narrow: the
# value can reach a capture library, so only a conservative alphabet is allowed.
_INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.:{}\\-]{1,64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")

MAX_TEXT_LEN = 500

#: Protocols the capture layer understands. Anything else is rejected outright
#: rather than passed through to a filter expression.
SUPPORTED_PROTOCOLS = ("tcp", "udp", "icmp")

#: Ranges reserved for documentation and examples (RFC 5737, RFC 3849). No real
#: infrastructure lives here, which is what makes them safe defaults for
#: generated scenarios.
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)


def is_documentation_address(addr: IPAddress) -> bool:
    """True for addresses reserved for documentation, which cannot be real hosts."""
    return any(addr.version == net.version and addr in net for net in DOCUMENTATION_NETWORKS)


def parse_ip(value: object, *, field: str = "ip") -> IPAddress:
    """Return ``value`` as an IP address object, or raise :class:`ValidationError`."""
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return value
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValidationError(f"{field} must not be empty")
    if len(text) > 45:  # longest possible textual IPv6 address
        raise ValidationError(f"{field} is too long to be an IP address")
    # Reject the "1.2.3" / octal / integer forms that inet_aton would accept but
    # that mean different things to different resolvers.
    try:
        return ipaddress.ip_address(text)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid IP address: {text!r}") from exc


def parse_network(value: object, *, field: str = "cidr") -> IPNetwork:
    """Return ``value`` as an IP network, accepting a bare address as a /32 or /128."""
    if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return value
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValidationError(f"{field} must not be empty")
    if len(text) > 49:
        raise ValidationError(f"{field} is too long to be a CIDR range")
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid CIDR range: {text!r}") from exc


def address_scope(addr: IPAddress) -> str:
    """Classify an address so policy can treat public space differently.

    Returns one of ``loopback``, ``private``, ``link-local``, ``multicast``,
    ``reserved``, or ``public``.
    """
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link-local"
    if addr.is_multicast:
        return "multicast"
    if addr.is_private:
        return "private"
    if addr.is_reserved or addr.is_unspecified:
        return "reserved"
    return "public"


def network_scope(net: IPNetwork) -> str:
    """Classify a network by the widest scope any of its addresses fall into."""
    scope = address_scope(net.network_address)
    if scope == "public" or address_scope(net.broadcast_address) == "public":
        return "public"
    return scope


def is_monitorable(addr: IPAddress) -> bool:
    """Reject addresses that can never be a meaningful monitoring target."""
    return not (addr.is_unspecified or addr.is_reserved or addr.is_multicast)


def validate_interface(value: object) -> str:
    """Validate a capture interface name."""
    if not isinstance(value, str):
        raise ValidationError("interface must be a string")
    text = value.strip()
    if not _INTERFACE_RE.match(text):
        raise ValidationError(f"interface name is not acceptable: {text!r}")
    return text


def validate_identifier(value: object, *, field: str = "id") -> str:
    """Validate a short machine-facing identifier (monitor ids, rule names)."""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    text = value.strip()
    if not _IDENTIFIER_RE.match(text):
        raise ValidationError(
            f"{field} must be 1-64 characters of letters, digits, '-' or '_'"
        )
    return text


def validate_username(value: object) -> str:
    """Validate a dashboard account name."""
    if not isinstance(value, str):
        raise ValidationError("username must be a string")
    text = value.strip().lower()
    if not _USERNAME_RE.match(text):
        raise ValidationError(
            "username must be 3-32 characters, starting with a letter or digit, "
            "using only letters, digits, '.', '_' or '-'"
        )
    return text


def validate_text(value: object, *, field: str, max_len: int = MAX_TEXT_LEN,
                  required: bool = True, min_len: int = 0) -> str:
    """Validate free-form operator text (justification, notes, labels).

    Control characters are rejected rather than stripped so that log-injection
    attempts fail loudly instead of being silently rewritten.
    """
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    text = value.strip()
    if required and not text:
        raise ValidationError(f"{field} is required")
    if len(text) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters")
    if len(text) < min_len:
        raise ValidationError(f"{field} must be at least {min_len} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValidationError(f"{field} must not contain control characters")
    return text


def validate_protocols(value: object) -> tuple[str, ...]:
    """Validate a protocol selection against the fixed supported vocabulary."""
    if value is None:
        return tuple(SUPPORTED_PROTOCOLS)
    if isinstance(value, str):
        items: Sequence[object] = [part for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValidationError("protocols must be a list or comma-separated string")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValidationError("protocol entries must be strings")
        name = item.strip().lower()
        if name not in SUPPORTED_PROTOCOLS:
            raise ValidationError(
                f"unsupported protocol {name!r}; choose from {', '.join(SUPPORTED_PROTOCOLS)}"
            )
        if name not in out:
            out.append(name)
    if not out:
        return tuple(SUPPORTED_PROTOCOLS)
    return tuple(out)


def validate_int(value: object, *, field: str, minimum: int, maximum: int,
                 default: int | None = None) -> int:
    """Validate and clamp-check an integer within an inclusive range."""
    if value is None or value == "":
        if default is None:
            raise ValidationError(f"{field} is required")
        return default
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be an integer") from exc
    if not minimum <= number <= maximum:
        raise ValidationError(f"{field} must be between {minimum} and {maximum}")
    return number


def validate_bool(value: object, *, field: str, default: bool | None = None) -> bool:
    """Validate a boolean supplied as a real bool or as form/env text."""
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        if default is None:
            raise ValidationError(f"{field} is required")
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    raise ValidationError(f"{field} must be a boolean")


def build_capture_filter(target: IPAddress, protocols: Iterable[str]) -> str:
    """Render a BPF filter for one target from validated inputs only.

    The returned expression is assembled from :func:`ipaddress` output and a
    hard-coded protocol vocabulary. No caller-supplied text reaches the result,
    so a hostile ``target`` or ``protocols`` value cannot inject filter syntax.
    """
    addr = parse_ip(target, field="target")
    protos = validate_protocols(list(protocols))
    family = "ip6" if addr.version == 6 else "ip"
    # str() on an ipaddress object emits only its canonical form.
    host = f"{family} host {addr}"
    if set(protos) == set(SUPPORTED_PROTOCOLS):
        return host
    icmp_token = "icmp6" if addr.version == 6 else "icmp"
    tokens = [icmp_token if proto == "icmp" else proto for proto in protos]
    proto_expr = " or ".join(tokens)
    return f"{host} and ({proto_expr})"
