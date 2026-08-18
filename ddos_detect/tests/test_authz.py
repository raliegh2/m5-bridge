"""The authorization gate: who may be monitored, and on what evidence."""

from __future__ import annotations

import time

import pytest

from ddos_detect.authz import AuthorizationLedger
from ddos_detect.config import Settings
from ddos_detect.errors import AuthorizationError, ValidationError

JUSTIFICATION = "Lab segment for the perimeter test, ticket OPS-4821"


def ledger_for(app) -> AuthorizationLedger:
    return app.ledger


class TestGrant:
    def test_records_an_entry(self, app):
        entry = app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=True)
        assert entry["cidr"] == "10.10.0.0/16"
        assert entry["scope"] == "private"
        assert entry["expires_at"] > time.time()

    def test_requires_the_attestation(self, app):
        with pytest.raises(ValidationError, match="attestation"):
            app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=False)

    def test_requires_a_real_justification(self, app):
        with pytest.raises(ValidationError):
            app.ledger.grant("10.10.0.0/16", "ok", "admin", attestation=True)

    def test_rejects_a_default_route(self, app):
        with pytest.raises(ValidationError, match="default route"):
            app.ledger.grant("0.0.0.0/0", JUSTIFICATION, "admin", attestation=True)

    def test_rejects_an_overly_broad_private_range(self, app):
        with pytest.raises(ValidationError, match="too broad"):
            app.ledger.grant("10.0.0.0/4", JUSTIFICATION, "admin", attestation=True)

    def test_rejects_public_space_by_default(self, app):
        with pytest.raises(ValidationError, match="public address space"):
            app.ledger.grant("8.8.8.0/24", JUSTIFICATION, "admin", attestation=True)

    def test_public_space_still_needs_a_reasonable_prefix(self, tmp_path):
        from ddos_detect.app import Application

        settings = Settings(data_dir=tmp_path / "d", bind_port=0, kdf_iterations=1000,
                            allow_public_targets=True)
        app = Application.build(settings)
        try:
            with pytest.raises(ValidationError, match="too broad"):
                app.ledger.grant("8.0.0.0/8", JUSTIFICATION, "admin", attestation=True)
            entry = app.ledger.grant("8.8.8.0/24", JUSTIFICATION, "admin", attestation=True)
            assert entry["scope"] == "public"
        finally:
            app.close()

    def test_expiry_is_capped_by_policy(self, app):
        with pytest.raises(ValidationError):
            app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=True,
                             days=9999)

    def test_grant_is_audited(self, app):
        app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=True)
        actions = [r.action for r in app.audit.read(20)]
        assert "authorization.granted" in actions


class TestCheck:
    def test_allows_an_address_inside_an_authorized_range(self, app):
        app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=True)
        decision = app.ledger.check("10.10.5.5", actor="operator")
        assert decision.enforced is True
        assert decision.cidr == "10.10.0.0/16"

    def test_refuses_an_address_outside_every_range(self, app):
        app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=True)
        with pytest.raises(AuthorizationError, match="not covered"):
            app.ledger.check("10.20.5.5", actor="operator")

    def test_refuses_with_no_entries_at_all(self, app):
        with pytest.raises(AuthorizationError):
            app.ledger.check("10.10.5.5", actor="operator")

    def test_refuses_public_addresses(self, app):
        with pytest.raises(AuthorizationError, match="public address"):
            app.ledger.check("8.8.8.8", actor="operator")

    def test_refuses_multicast_and_unspecified(self, app):
        for value in ("224.0.0.1", "0.0.0.0"):
            with pytest.raises(AuthorizationError):
                app.ledger.check(value, actor="operator")

    def test_rejects_malformed_targets(self, app):
        with pytest.raises(ValidationError):
            app.ledger.check("10.10.5.5; drop table", actor="operator")

    def test_revoked_entries_stop_authorizing(self, app):
        entry = app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=True)
        assert app.ledger.check("10.10.5.5", actor="operator").enforced
        app.ledger.revoke(entry["id"], "admin")
        with pytest.raises(AuthorizationError):
            app.ledger.check("10.10.5.5", actor="operator")

    def test_expired_entries_stop_authorizing(self, app):
        entry = app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=True)
        # Age the entry past its expiry rather than waiting for it.
        app.store._execute(
            "UPDATE authorizations SET expires_at = ? WHERE id = ?",
            (time.time() - 1, entry["id"]),
        )
        with pytest.raises(AuthorizationError):
            app.ledger.check("10.10.5.5", actor="operator")

    def test_ipv4_entry_does_not_authorize_ipv6(self, app):
        app.ledger.grant("10.10.0.0/16", JUSTIFICATION, "admin", attestation=True)
        with pytest.raises(AuthorizationError):
            app.ledger.check("fd00::1", actor="operator")

    def test_denials_are_audited(self, app):
        with pytest.raises(AuthorizationError):
            app.ledger.check("10.10.5.5", actor="operator")
        actions = [r.action for r in app.audit.read(20)]
        assert "authorization.denied" in actions

    def test_bypass_mode_is_recorded(self, tmp_path):
        from ddos_detect.app import Application

        settings = Settings(data_dir=tmp_path / "d", bind_port=0, kdf_iterations=1000,
                            require_authorization=False)
        app = Application.build(settings)
        try:
            decision = app.ledger.check("10.10.5.5", actor="operator")
            assert decision.enforced is False
            actions = [r.action for r in app.audit.read(20)]
            assert "authorization.bypassed" in actions
        finally:
            app.close()

    def test_bypass_mode_still_refuses_public_targets(self, tmp_path):
        from ddos_detect.app import Application

        settings = Settings(data_dir=tmp_path / "d", bind_port=0, kdf_iterations=1000,
                            require_authorization=False)
        app = Application.build(settings)
        try:
            with pytest.raises(AuthorizationError):
                app.ledger.check("8.8.8.8", actor="operator")
        finally:
            app.close()
