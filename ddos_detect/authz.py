"""Authorization ledger for monitoring targets.

The dashboard lets an operator point the detector at any IP address they need
to watch. That capability is gated here, because observing traffic to an
address you do not run is a different act from observing your own.

Before a target can be monitored it must be covered by a ledger entry, and
creating one requires:

* an explicit attestation that the operator is authorised to monitor the range;
* a written justification, retained with the entry;
* an expiry, so authorisation is re-attested rather than granted once forever.

Public address space carries an extra gate (:attr:`Settings.allow_public_targets`)
and a minimum prefix length, since a public range is far more likely to belong
to somebody else and a short prefix would authorise a swathe of the internet in
one entry. Every grant, revocation, and denial is written to the audit log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .audit import AuditLog
from .config import Settings
from .errors import AuthorizationError, ValidationError
from .store import Store
from .validation import (
    IPAddress,
    address_scope,
    is_monitorable,
    network_scope,
    parse_ip,
    parse_network,
    validate_bool,
    validate_int,
    validate_text,
)

#: Shortest prefix accepted per scope, so one entry cannot authorise the world.
MIN_PREFIX = {
    4: {"public": 16, "private": 8, "loopback": 8, "link-local": 16},
    6: {"public": 48, "private": 32, "loopback": 64, "link-local": 64},
}

MIN_JUSTIFICATION = 12

ATTESTATION_TEXT = (
    "I confirm I own this address range or have documented permission from its "
    "operator to monitor traffic to it."
)


@dataclass(frozen=True)
class Decision:
    """Result of an authorization check."""

    target: str
    scope: str
    authorization_id: int | None
    cidr: str | None
    expires_at: float | None
    enforced: bool

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "scope": self.scope,
            "authorization_id": self.authorization_id,
            "cidr": self.cidr,
            "expires_at": self.expires_at,
            "enforced": self.enforced,
        }


class AuthorizationLedger:
    def __init__(self, store: Store, settings: Settings, audit: AuditLog) -> None:
        self._store = store
        self._settings = settings
        self._audit = audit

    # -- granting --------------------------------------------------------
    def grant(self, cidr: object, justification: object, actor: str, *,
              attestation: object, days: object = None) -> dict:
        """Record authorisation for a range. Returns the stored entry."""
        network = parse_network(cidr, field="cidr")
        scope = network_scope(network)
        justification = validate_text(
            justification, field="justification", min_len=MIN_JUSTIFICATION, max_len=500
        )
        if not validate_bool(attestation, field="attestation", default=False):
            raise ValidationError(
                "authorization requires an explicit attestation: " + ATTESTATION_TEXT
            )
        max_days = self._settings.authorization_max_days
        days_int = validate_int(days, field="days", minimum=1, maximum=max_days,
                                default=min(30, max_days))

        if network.prefixlen == 0:
            raise ValidationError("refusing to authorise a default route")
        minimum = MIN_PREFIX.get(network.version, {}).get(scope, 16)
        if network.prefixlen < minimum:
            raise ValidationError(
                f"{network} is too broad for {scope} space: authorise /{minimum} or narrower"
            )
        if scope == "public" and not self._settings.allow_public_targets:
            self._audit.record(
                "authorization.denied", actor=actor, cidr=str(network), scope=scope,
                reason="public targets disabled",
            )
            raise ValidationError(
                f"{network} is public address space. Monitoring public targets is disabled; "
                "enable DDOS_ALLOW_PUBLIC_TARGETS only for ranges you operate."
            )
        if scope in ("multicast", "reserved"):
            raise ValidationError(f"{network} is {scope} space and cannot be a target")

        expires_at = time.time() + days_int * 86400
        auth_id = self._store.add_authorization(
            cidr=str(network), scope=scope, justification=justification,
            created_by=actor, expires_at=expires_at,
        )
        self._audit.record(
            "authorization.granted", actor=actor, id=auth_id, cidr=str(network), scope=scope,
            days=days_int, justification=justification,
        )
        return {
            "id": auth_id, "cidr": str(network), "scope": scope,
            "justification": justification, "created_by": actor,
            "created_at": time.time(), "expires_at": expires_at,
        }

    def revoke(self, auth_id: object, actor: str) -> bool:
        ident = validate_int(auth_id, field="id", minimum=1, maximum=2**31)
        revoked = self._store.revoke_authorization(ident, actor)
        self._audit.record(
            "authorization.revoked" if revoked else "authorization.revoke_noop",
            actor=actor, id=ident,
        )
        return revoked

    def list(self, include_inactive: bool = False) -> list[dict]:
        return self._store.list_authorizations(include_inactive=include_inactive)

    # -- checking --------------------------------------------------------
    def check(self, target: object, actor: str = "system") -> Decision:
        """Authorise monitoring of ``target`` or raise :class:`AuthorizationError`."""
        addr = parse_ip(target, field="target")
        scope = address_scope(addr)

        if not is_monitorable(addr):
            raise AuthorizationError(f"{addr} ({scope}) is not a valid monitoring target")

        if scope == "public" and not self._settings.allow_public_targets:
            self._deny(addr, actor, "public targets disabled")
            raise AuthorizationError(
                f"{addr} is a public address. This system will not monitor public targets "
                "unless the operator enables DDOS_ALLOW_PUBLIC_TARGETS and records an "
                "authorization entry for the range."
            )

        if not self._settings.require_authorization:
            # Explicitly unsafe mode: still audited so the gap is visible later.
            self._audit.record(
                "authorization.bypassed", actor=actor, target=str(addr), scope=scope,
            )
            return Decision(str(addr), scope, None, None, None, enforced=False)

        entry = self._match(addr)
        if entry is None:
            self._deny(addr, actor, "no active authorization entry")
            raise AuthorizationError(
                f"{addr} is not covered by an active authorization entry. Record one with "
                "the attestation and justification first, then start the monitor."
            )
        return Decision(
            target=str(addr), scope=scope, authorization_id=int(entry["id"]),
            cidr=str(entry["cidr"]), expires_at=float(entry["expires_at"]), enforced=True,
        )

    def _match(self, addr: IPAddress) -> dict | None:
        for entry in self._store.list_authorizations():
            try:
                network = parse_network(entry["cidr"])
            except ValidationError:
                continue
            if network.version != addr.version:
                continue
            if addr in network:
                return entry
        return None

    def _deny(self, addr: IPAddress, actor: str, reason: str) -> None:
        self._audit.record(
            "authorization.denied", actor=actor, target=str(addr), reason=reason,
        )
