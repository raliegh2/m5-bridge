"""Accounts, sessions, and CSRF tokens.

Design notes
------------
* Passwords are stored as PBKDF2-HMAC-SHA256 with a per-password salt.
* Session tokens are random 256-bit values; only their SHA-256 hash is stored,
  so read access to the database does not yield usable sessions.
* Login failures are counted per account and lock it temporarily. Responses
  are identical whether the account exists, is locked, or the password is
  wrong, and a dummy KDF run keeps the timing flat for unknown accounts.
* Sessions have both an absolute lifetime and an idle timeout.
* Every state-changing request must carry a CSRF token bound to the session.
* Two roles exist: ``admin`` may authorise targets and manage accounts;
  ``viewer`` may only read. Starting a monitor requires ``operator`` or above.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from .audit import AuditLog
from .config import Settings
from .crypto import constant_time_equals, generate_token, hash_password, verify_password
from .errors import AuthenticationError, ValidationError
from .store import Store, UserRow
from .validation import validate_text, validate_username

ROLES = ("admin", "operator", "viewer")
ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}

#: Compared against on unknown usernames so the response time does not reveal
#: whether an account exists.
_DUMMY_HASH = (
    "pbkdf2_sha256$100000$AAAAAAAAAAAAAAAAAAAAAA==$"
    "Ag9K5s9M0v6qVh0mVdQ4rC3wQ4mQeQ8dU2mQeQ8dU2k="
)

GENERIC_LOGIN_ERROR = "invalid username or password"


@dataclass(frozen=True)
class Principal:
    """The authenticated identity behind a request."""

    username: str
    role: str
    csrf_token: str
    session_hash: str

    def can(self, required: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK.get(required, 99)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthManager:
    def __init__(self, store: Store, settings: Settings, audit: AuditLog) -> None:
        self._store = store
        self._settings = settings
        self._audit = audit

    # -- accounts --------------------------------------------------------
    def create_user(self, username: object, password: str, role: str = "viewer",
                    actor: str = "system") -> str:
        name = validate_username(username)
        if role not in ROLES:
            raise ValidationError(f"role must be one of: {', '.join(ROLES)}")
        _check_password_strength(password, name)
        if self._store.get_user(name) is not None:
            raise ValidationError(f"user {name} already exists")
        encoded = hash_password(password, iterations=self._settings.kdf_iterations)
        self._store.create_user(name, encoded, role)
        self._audit.record("user.created", actor=actor, username=name, role=role)
        return name

    def set_password(self, username: object, password: str, actor: str = "system") -> None:
        name = validate_username(username)
        user = self._store.get_user(name)
        if user is None:
            raise ValidationError(f"user {name} does not exist")
        _check_password_strength(password, name)
        encoded = hash_password(password, iterations=self._settings.kdf_iterations)
        self._store.set_password(name, encoded)
        # Any existing session is invalidated: a password change must evict
        # whoever might already be holding a session for that account.
        self._store.delete_sessions_for_user(user.id)
        self._audit.record("user.password_changed", actor=actor, username=name)

    def has_users(self) -> bool:
        return bool(self._store.list_users())

    def list_users(self) -> list[dict]:
        return self._store.list_users()

    # -- login -----------------------------------------------------------
    def login(self, username: object, password: object, client: str = "") -> tuple[str, Principal]:
        """Authenticate and open a session. Returns ``(token, principal)``."""
        try:
            name = validate_username(username)
        except ValidationError:
            verify_password("x" * 16, _DUMMY_HASH)  # keep timing flat
            raise AuthenticationError(GENERIC_LOGIN_ERROR) from None
        if not isinstance(password, str):
            raise AuthenticationError(GENERIC_LOGIN_ERROR)

        user = self._store.get_user(name)
        now = time.time()
        if user is None:
            verify_password(password, _DUMMY_HASH)
            self._audit.record("login.failed", actor=name, reason="unknown user", client=client)
            raise AuthenticationError(GENERIC_LOGIN_ERROR)
        if user.disabled:
            verify_password(password, _DUMMY_HASH)
            self._audit.record("login.failed", actor=name, reason="disabled", client=client)
            raise AuthenticationError(GENERIC_LOGIN_ERROR)
        if user.locked_until > now:
            verify_password(password, _DUMMY_HASH)
            self._audit.record("login.blocked", actor=name, reason="locked", client=client)
            raise AuthenticationError(GENERIC_LOGIN_ERROR)
        if not verify_password(password, user.password_hash):
            self._store.record_login_failure(
                name, self._settings.lockout_seconds, self._settings.max_login_attempts
            )
            self._audit.record("login.failed", actor=name, reason="bad password", client=client)
            raise AuthenticationError(GENERIC_LOGIN_ERROR)

        self._store.record_login_success(name)
        token = generate_token()
        csrf = generate_token()
        expires_at = now + self._settings.session_ttl_seconds
        self._store.create_session(
            hash_token(token), user.id, csrf, expires_at, client[:120]
        )
        self._audit.record("login.succeeded", actor=name, client=client)
        return token, Principal(name, user.role, csrf, hash_token(token))

    def logout(self, token: str, actor: str = "") -> None:
        self._store.delete_session(hash_token(token))
        if actor:
            self._audit.record("logout", actor=actor)

    # -- session validation ----------------------------------------------
    def authenticate(self, token: object) -> Principal:
        """Resolve a session token to a :class:`Principal`."""
        if not isinstance(token, str) or not token:
            raise AuthenticationError("authentication required")
        token_hash = hash_token(token)
        row = self._store.get_session(token_hash)
        if row is None:
            raise AuthenticationError("session not found")
        now = time.time()
        if float(row["expires_at"]) <= now:
            self._store.delete_session(token_hash)
            raise AuthenticationError("session expired")
        idle_limit = self._settings.session_idle_timeout_seconds
        if idle_limit and now - float(row["last_seen"]) > idle_limit:
            self._store.delete_session(token_hash)
            raise AuthenticationError("session timed out")
        user: UserRow | None = self._store.get_user_by_id(int(row["user_id"]))
        if user is None or user.disabled:
            self._store.delete_session(token_hash)
            raise AuthenticationError("account is not available")
        self._store.touch_session(token_hash, now)
        return Principal(user.username, user.role, str(row["csrf_token"]), token_hash)

    @staticmethod
    def check_csrf(principal: Principal, supplied: object) -> None:
        """Verify the CSRF token on a state-changing request."""
        if not isinstance(supplied, str) or not supplied:
            raise AuthenticationError("missing CSRF token")
        if not constant_time_equals(principal.csrf_token, supplied):
            raise AuthenticationError("invalid CSRF token")

    @staticmethod
    def require_role(principal: Principal, role: str) -> None:
        if not principal.can(role):
            raise AuthenticationError(f"this action requires the {role} role")


def _check_password_strength(password: object, username: str) -> None:
    """Reject the passwords that actually get compromised.

    Length is the dominant factor, so the rule is a 12-character minimum rather
    than a character-class checklist; the rest blocks the specific failures
    seen in practice - the username as the password, and known-common choices.
    """
    text = validate_text(password, field="password", max_len=1024, min_len=12)
    lowered = text.lower()
    if username and username.lower() in lowered:
        raise ValidationError("password must not contain the username")
    common = {
        "password", "passw0rd", "letmein", "changeme", "administrator",
        "qwerty", "123456", "iloveyou", "welcome", "monitoring", "ddosdetect",
    }
    stripped = "".join(ch for ch in lowered if ch.isalnum())
    if any(word in stripped for word in common) and len(text) < 20:
        raise ValidationError("password contains a well-known word; choose a longer passphrase")
    if len(set(text)) < 5:
        raise ValidationError("password is too repetitive")
