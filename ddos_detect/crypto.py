"""Key management, password hashing, and pseudonymisation helpers.

Only standard-library primitives are used: PBKDF2-HMAC-SHA256 for passwords,
HMAC-SHA256 for the audit chain and for source-address pseudonymisation, and
:mod:`secrets` for token generation. Every secret comparison goes through
:func:`hmac.compare_digest` so no code path leaks information by timing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

from .errors import ConfigError, ValidationError

SECRET_BYTES = 32
SALT_BYTES = 16
TOKEN_BYTES = 32


def generate_token() -> str:
    """Return a URL-safe random token with 256 bits of entropy."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking their contents through timing."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def load_or_create_secret(path: Path) -> bytes:
    """Load the per-installation secret, creating it on first use.

    The file is created with owner-only permissions and re-checked on every
    load; a world-readable secret is treated as a configuration error rather
    than silently accepted, because it protects the audit chain.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _require_private(path)
        raw = path.read_bytes().strip()
        try:
            secret = base64.urlsafe_b64decode(raw)
        except Exception as exc:  # noqa: BLE001 - any decode failure is fatal
            raise ConfigError(f"instance secret at {path} is corrupt") from exc
        if len(secret) < SECRET_BYTES:
            raise ConfigError(f"instance secret at {path} is too short")
        return secret

    secret = secrets.token_bytes(SECRET_BYTES)
    # Create with 0600 from the outset rather than chmod-ing after the fact,
    # which would leave a window where the secret is readable.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, base64.urlsafe_b64encode(secret))
    finally:
        os.close(fd)
    _harden(path)
    return secret


def _harden(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except (OSError, NotImplementedError):
        # Windows without POSIX ACL emulation: permissions are inherited from
        # the parent directory, which the CLI documents as needing to be private.
        pass


def _require_private(path: Path) -> None:
    if os.name != "posix":
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"instance secret at {path} is accessible to other users; "
            "run: chmod 600 " + str(path)
        )


def hash_password(password: str, *, iterations: int, salt: bytes | None = None) -> str:
    """Hash a password for storage.

    Returns ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``.
    """
    if not isinstance(password, str) or len(password) < 12:
        raise ValidationError("password must be at least 12 characters")
    if len(password) > 1024:
        # Bound the work an unauthenticated caller can force the KDF to do.
        raise ValidationError("password must be at most 1024 characters")
    salt = salt or secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(iterations),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against a stored hash in constant time."""
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    if len(password) > 1024:
        return False
    parts = encoded.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2])
        expected = base64.b64decode(parts[3])
    except (ValueError, TypeError):
        return False
    if iterations < 1000 or iterations > 5_000_000:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


def pseudonymise(value: str, secret: bytes) -> str:
    """Return a stable keyed pseudonym for an address.

    Keyed so the mapping cannot be reversed by brute-forcing the (small) IPv4
    space, stable so top-talker counting still works over the retention window.
    """
    mac = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).digest()
    return "anon:" + base64.urlsafe_b64encode(mac[:12]).decode("ascii").rstrip("=")


def chain_digest(secret: bytes, previous: str, payload: str) -> str:
    """Return the keyed digest linking one audit record to the previous one."""
    mac = hmac.new(secret, (previous + "|" + payload).encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()
