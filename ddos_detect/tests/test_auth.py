"""Accounts, sessions, CSRF, and the audit chain."""

from __future__ import annotations

import time

import pytest

from ddos_detect.audit import AuditLog
from ddos_detect.auth import GENERIC_LOGIN_ERROR
from ddos_detect.crypto import hash_password, pseudonymise, verify_password
from ddos_detect.errors import AuthenticationError, ValidationError
from ddos_detect.ratelimit import RateLimiter
from ddos_detect.tests.conftest import ADMIN_PASSWORD

GOOD_PASSWORD = "seven-mountains-quiet-river"


class TestPasswordStorage:
    def test_hash_verifies(self):
        encoded = hash_password(GOOD_PASSWORD, iterations=1000)
        assert verify_password(GOOD_PASSWORD, encoded)
        assert not verify_password(GOOD_PASSWORD + "x", encoded)

    def test_hash_is_salted(self):
        a = hash_password(GOOD_PASSWORD, iterations=1000)
        b = hash_password(GOOD_PASSWORD, iterations=1000)
        assert a != b

    def test_plaintext_never_appears_in_the_hash(self):
        assert GOOD_PASSWORD not in hash_password(GOOD_PASSWORD, iterations=1000)

    @pytest.mark.parametrize("bad", ["", "short", "x" * 11, 12345, None])
    def test_rejects_weak_or_wrong_types(self, bad):
        with pytest.raises(ValidationError):
            hash_password(bad, iterations=1000)

    @pytest.mark.parametrize("encoded", ["", "nonsense", "pbkdf2_sha256$1$a$b", "a$b$c$d"])
    def test_malformed_stored_hashes_never_verify(self, encoded):
        assert verify_password(GOOD_PASSWORD, encoded) is False


class TestAccountRules:
    def test_password_must_not_contain_the_username(self, app):
        with pytest.raises(ValidationError, match="username"):
            app.auth.create_user("analyst", "analyst-analyst-1", "viewer")

    def test_password_must_not_be_a_common_choice(self, app):
        with pytest.raises(ValidationError):
            app.auth.create_user("analyst", "password12345", "viewer")

    def test_password_must_not_be_repetitive(self, app):
        with pytest.raises(ValidationError):
            app.auth.create_user("analyst", "aaaaaaaaaaaaaaaa", "viewer")

    def test_duplicate_users_are_refused(self, app, admin):
        with pytest.raises(ValidationError, match="already exists"):
            app.auth.create_user("admin", GOOD_PASSWORD, "viewer")

    def test_unknown_role_is_refused(self, app):
        with pytest.raises(ValidationError):
            app.auth.create_user("analyst", GOOD_PASSWORD, "superuser")


class TestLogin:
    def test_successful_login_returns_a_session(self, app, admin):
        token, principal = app.auth.login("admin", ADMIN_PASSWORD)
        assert token
        assert principal.username == "admin"
        assert principal.role == "admin"
        assert principal.csrf_token

    def test_session_token_is_not_stored_verbatim(self, app, admin):
        token, principal = app.auth.login("admin", ADMIN_PASSWORD)
        assert app.store.get_session(token) is None  # only its hash is stored
        assert app.store.get_session(principal.session_hash) is not None

    @pytest.mark.parametrize("username,password", [
        ("admin", "wrong-password-entirely"),
        ("nosuchuser", ADMIN_PASSWORD),
        ("", ADMIN_PASSWORD),
        ("admin", None),
    ])
    def test_failures_are_indistinguishable(self, app, admin, username, password):
        with pytest.raises(AuthenticationError) as exc:
            app.auth.login(username, password)
        assert str(exc.value) == GENERIC_LOGIN_ERROR

    def test_repeated_failures_lock_the_account(self, app, admin):
        for _ in range(app.settings.max_login_attempts):
            with pytest.raises(AuthenticationError):
                app.auth.login("admin", "wrong-password-entirely")
        # Correct password now also fails: the account is locked.
        with pytest.raises(AuthenticationError):
            app.auth.login("admin", ADMIN_PASSWORD)
        assert any(r.action == "login.blocked" for r in app.audit.read(50))

    def test_successful_login_clears_the_failure_count(self, app, admin):
        with pytest.raises(AuthenticationError):
            app.auth.login("admin", "wrong-password-entirely")
        app.auth.login("admin", ADMIN_PASSWORD)
        assert app.store.get_user("admin").failed_attempts == 0


class TestSessions:
    def test_authenticate_resolves_a_live_session(self, app, admin):
        token, _ = app.auth.login("admin", ADMIN_PASSWORD)
        assert app.auth.authenticate(token).username == "admin"

    @pytest.mark.parametrize("token", ["", None, "not-a-token", 12345])
    def test_bad_tokens_are_refused(self, app, admin, token):
        with pytest.raises(AuthenticationError):
            app.auth.authenticate(token)

    def test_logout_invalidates_the_session(self, app, admin):
        token, _ = app.auth.login("admin", ADMIN_PASSWORD)
        app.auth.logout(token, "admin")
        with pytest.raises(AuthenticationError):
            app.auth.authenticate(token)

    def test_expired_sessions_are_refused_and_deleted(self, app, admin):
        token, principal = app.auth.login("admin", ADMIN_PASSWORD)
        app.store._execute(
            "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
            (time.time() - 1, principal.session_hash),
        )
        with pytest.raises(AuthenticationError, match="expired"):
            app.auth.authenticate(token)
        assert app.store.get_session(principal.session_hash) is None

    def test_idle_sessions_time_out(self, app, admin):
        token, principal = app.auth.login("admin", ADMIN_PASSWORD)
        stale = time.time() - app.settings.session_idle_timeout_seconds - 10
        app.store._execute(
            "UPDATE sessions SET last_seen = ? WHERE token_hash = ?",
            (stale, principal.session_hash),
        )
        with pytest.raises(AuthenticationError, match="timed out"):
            app.auth.authenticate(token)

    def test_password_change_revokes_existing_sessions(self, app, admin):
        token, _ = app.auth.login("admin", ADMIN_PASSWORD)
        app.auth.set_password("admin", "a-completely-different-passphrase", actor="admin")
        with pytest.raises(AuthenticationError):
            app.auth.authenticate(token)


class TestCsrfAndRoles:
    def test_csrf_token_must_match(self, app, admin):
        _, principal = app.auth.login("admin", ADMIN_PASSWORD)
        app.auth.check_csrf(principal, principal.csrf_token)  # no raise
        for bad in ("", None, "wrong", principal.csrf_token + "x"):
            with pytest.raises(AuthenticationError):
                app.auth.check_csrf(principal, bad)

    def test_role_hierarchy(self, app):
        app.auth.create_user("viewer1", GOOD_PASSWORD, "viewer")
        app.auth.create_user("op1", GOOD_PASSWORD, "operator")
        _, viewer = app.auth.login("viewer1", GOOD_PASSWORD)
        _, operator = app.auth.login("op1", GOOD_PASSWORD)
        assert viewer.can("viewer") and not viewer.can("operator")
        assert operator.can("operator") and not operator.can("admin")
        with pytest.raises(AuthenticationError):
            app.auth.require_role(viewer, "operator")


class TestAuditChain:
    def test_chain_verifies_when_untouched(self, app, admin):
        app.audit.record("test.event", actor="tester", note="one")
        app.audit.record("test.event", actor="tester", note="two")
        ok, detail = app.audit.verify_chain()
        assert ok, detail

    def test_edited_record_is_detected(self, app):
        app.audit.record("authorization.granted", actor="admin", cidr="10.0.0.0/8")
        app.audit.record("monitor.started", actor="admin", target="10.0.0.5")
        path = app.audit.path
        text = path.read_text(encoding="utf-8").replace("10.0.0.5", "10.0.0.9")
        path.write_text(text, encoding="utf-8")
        ok, detail = app.audit.verify_chain()
        assert not ok
        assert "altered" in detail or "link" in detail

    def test_deleted_record_is_detected(self, app):
        for i in range(4):
            app.audit.record("test.event", actor="tester", index=i)
        path = app.audit.path
        lines = path.read_text(encoding="utf-8").splitlines()
        del lines[1]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, detail = app.audit.verify_chain()
        assert not ok

    def test_reordered_records_are_detected(self, app):
        for i in range(4):
            app.audit.record("test.event", actor="tester", index=i)
        path = app.audit.path
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1], lines[2] = lines[2], lines[1]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert app.audit.verify_chain()[0] is False

    def test_a_forged_record_needs_the_secret(self, app, tmp_path):
        app.audit.record("test.event", actor="tester")
        # An attacker with file access but not the secret cannot extend the chain.
        forger = AuditLog(app.audit.path, b"the-wrong-secret-entirely-000000")
        forger.record("monitor.started", actor="mallory", target="10.0.0.1")
        ok, _ = app.audit.verify_chain()
        assert ok is False

    def test_detail_values_are_bounded(self, app):
        record = app.audit.record("test.event", actor="tester", blob="x" * 5000)
        assert len(record.detail["blob"]) <= 500

    def test_control_characters_are_refused(self, app):
        with pytest.raises(ValidationError):
            app.audit.record("test\nevent", actor="tester")


class TestRateLimiter:
    def test_allows_up_to_the_burst_then_refuses(self):
        limiter = RateLimiter(per_minute=60, burst=3)
        for _ in range(3):
            limiter.check("client", now=0.0)
        with pytest.raises(Exception):
            limiter.check("client", now=0.0)

    def test_tokens_refill_over_time(self):
        limiter = RateLimiter(per_minute=60, burst=1)
        limiter.check("client", now=0.0)
        with pytest.raises(Exception):
            limiter.check("client", now=0.0)
        limiter.check("client", now=1.1)

    def test_clients_are_independent(self):
        limiter = RateLimiter(per_minute=60, burst=1)
        limiter.check("a", now=0.0)
        limiter.check("b", now=0.0)

    def test_reset_clears_a_client(self):
        limiter = RateLimiter(per_minute=60, burst=1)
        limiter.check("a", now=0.0)
        limiter.reset("a")
        limiter.check("a", now=0.0)


class TestPseudonymisation:
    def test_is_stable_and_keyed(self):
        secret = b"0" * 32
        assert pseudonymise("10.0.0.1", secret) == pseudonymise("10.0.0.1", secret)
        assert pseudonymise("10.0.0.1", secret) != pseudonymise("10.0.0.2", secret)
        assert pseudonymise("10.0.0.1", secret) != pseudonymise("10.0.0.1", b"1" * 32)

    def test_does_not_contain_the_address(self):
        assert "10.0.0.1" not in pseudonymise("10.0.0.1", b"0" * 32)
