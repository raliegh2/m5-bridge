"""Configuration.

All tunables live here and are sourced from the environment with the ``DDOS_``
prefix, so nothing security-relevant is hard-coded at a call site. Defaults are
chosen to be safe rather than convenient: the dashboard binds to loopback, the
authorization ledger is enforced, public-address targets are refused, and
packet payloads are never captured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from .errors import ConfigError, ValidationError
from .validation import validate_bool, validate_int

ENV_PREFIX = "DDOS_"

#: Bytes captured per packet. Enough for Ethernet + IPv6 + TCP headers with
#: options and nothing more - payload bytes are never read into the process.
HEADER_ONLY_SNAPLEN = 128


def _env(name: str) -> str | None:
    return os.environ.get(ENV_PREFIX + name.upper())


@dataclass(frozen=True)
class Thresholds:
    """Detection thresholds.

    Absolute floors exist so that a quiet host cannot produce an alert purely
    from a large relative change: a jump from 2 to 40 packets per second is a
    20x anomaly but is not a denial-of-service event.
    """

    #: Packets/second of TCP SYN below which a SYN-flood signal never fires.
    syn_pps: float = 500.0
    #: Packets/second of UDP below which a UDP-flood signal never fires.
    udp_pps: float = 2000.0
    #: Packets/second of ICMP below which an ICMP-flood signal never fires.
    icmp_pps: float = 500.0
    #: Global packets/second floor for any statistical anomaly signal.
    pps_floor: float = 200.0
    #: Global bits/second floor for any statistical anomaly signal.
    bps_floor: float = 5_000_000.0
    #: Ratio of SYN to completed handshakes above which a half-open flood is implied.
    syn_ack_ratio: float = 3.0
    #: Share (0-1) of traffic from known amplifier source ports implying reflection.
    amplification_share: float = 0.5
    #: Mean packet size (bytes) above which amplified responses are implied.
    amplification_size: float = 500.0
    #: Distinct sources/second implying a *distributed* rather than single-source flood.
    distributed_sources: float = 50.0
    #: Z-score at which a statistical anomaly becomes a warning.
    zscore_warn: float = 4.0
    #: Z-score at which a statistical anomaly becomes critical.
    zscore_crit: float = 8.0
    #: Aggregate score (0-1) entering the SUSPECTED state.
    score_warn: float = 0.45
    #: Aggregate score (0-1) entering the ATTACK state.
    score_crit: float = 0.75
    #: Consecutive evaluations above a threshold before the state changes.
    consecutive_hits: int = 3
    #: Seconds an alert stays latched after signals fall back below threshold.
    cooldown_seconds: float = 30.0


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the whole system."""

    # --- server ---------------------------------------------------------
    #: Loopback by default. Binding to a routable address exposes the
    #: dashboard, so it is refused unless TLS terminates in front of it.
    bind_host: str = "127.0.0.1"
    bind_port: int = 8787
    allow_non_loopback_bind: bool = False
    session_ttl_seconds: int = 3600
    session_idle_timeout_seconds: int = 900
    secure_cookies: bool = False

    # --- storage --------------------------------------------------------
    data_dir: Path = Path("ddos_detect_data")
    retention_days: int = 7
    #: Store a keyed hash of observed source addresses instead of the address.
    #: Preserves top-talker counting while minimising retained personal data.
    anonymize_sources: bool = False

    # --- authentication and abuse control -------------------------------
    max_login_attempts: int = 5
    lockout_seconds: int = 300
    api_requests_per_minute: int = 240
    login_requests_per_minute: int = 10
    #: PBKDF2-HMAC-SHA256 iterations for password storage.
    kdf_iterations: int = 480_000

    # --- authorization policy -------------------------------------------
    #: Targets must appear in the authorization ledger. Disabling this is only
    #: sensible in an isolated lab and is recorded in the audit log.
    require_authorization: bool = True
    #: Monitoring a public address requires this to be enabled *and* a ledger
    #: entry, because a public target is far more likely to be third-party
    #: infrastructure the operator does not control.
    allow_public_targets: bool = False
    #: Ledger entries expire, forcing periodic re-attestation.
    authorization_max_days: int = 90

    # --- capture --------------------------------------------------------
    #: ``auto`` prefers a live backend and falls back to the offline one.
    capture_backend: str = "auto"
    interface: str = ""
    snaplen: int = HEADER_ONLY_SNAPLEN
    max_monitors: int = 8
    #: POSIX only: drop to this user after the capture socket is opened.
    drop_privileges_user: str = ""

    # --- detection ------------------------------------------------------
    #: Detection window. Kept short so a burst is measured rather than averaged
    #: away; the long-term picture comes from the EWMA baseline instead.
    window_seconds: int = 10
    bucket_seconds: int = 1
    learning_seconds: int = 60
    evaluate_interval_seconds: float = 1.0
    top_talkers: int = 10
    #: Cap on distinct source addresses tracked per monitor, bounding memory
    #: under a spoofed-source flood (the exact scenario being detected).
    max_tracked_sources: int = 20_000
    thresholds: Thresholds = field(default_factory=Thresholds)

    def __post_init__(self) -> None:
        if self.bucket_seconds <= 0:
            raise ConfigError("bucket_seconds must be positive")
        if self.window_seconds < self.bucket_seconds * 5:
            raise ConfigError("window_seconds must cover at least five buckets")
        if self.snaplen > 256:
            raise ConfigError(
                "snaplen above 256 would capture payload bytes; this system is "
                "header-only by design"
            )
        if self.retention_days < 1:
            raise ConfigError("retention_days must be at least 1")
        if not self.is_loopback_bind and not self.allow_non_loopback_bind:
            raise ConfigError(
                f"refusing to bind {self.bind_host}: the dashboard is unauthenticated "
                "at the network layer. Put a TLS reverse proxy in front of it and set "
                f"{ENV_PREFIX}ALLOW_NON_LOOPBACK_BIND=true to override."
            )

    @property
    def is_loopback_bind(self) -> bool:
        return self.bind_host in ("127.0.0.1", "::1", "localhost")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ddos_detect.db"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.log"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "instance_secret"

    @property
    def bucket_count(self) -> int:
        return max(5, self.window_seconds // self.bucket_seconds)

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "Settings":
        """Build settings from ``DDOS_*`` environment variables."""
        values: dict = {}
        try:
            for name, raw in (
                ("bind_port", _env("bind_port")),
                ("session_ttl_seconds", _env("session_ttl_seconds")),
                ("session_idle_timeout_seconds", _env("session_idle_timeout_seconds")),
                ("retention_days", _env("retention_days")),
                ("max_login_attempts", _env("max_login_attempts")),
                ("lockout_seconds", _env("lockout_seconds")),
                ("api_requests_per_minute", _env("api_requests_per_minute")),
                ("login_requests_per_minute", _env("login_requests_per_minute")),
                ("kdf_iterations", _env("kdf_iterations")),
                ("authorization_max_days", _env("authorization_max_days")),
                ("snaplen", _env("snaplen")),
                ("max_monitors", _env("max_monitors")),
                ("window_seconds", _env("window_seconds")),
                ("bucket_seconds", _env("bucket_seconds")),
                ("learning_seconds", _env("learning_seconds")),
                ("top_talkers", _env("top_talkers")),
                ("max_tracked_sources", _env("max_tracked_sources")),
            ):
                if raw is not None:
                    values[name] = validate_int(
                        raw, field=ENV_PREFIX + name.upper(), minimum=1, maximum=10_000_000
                    )
            for name in (
                "allow_non_loopback_bind",
                "secure_cookies",
                "anonymize_sources",
                "require_authorization",
                "allow_public_targets",
            ):
                raw = _env(name)
                if raw is not None:
                    values[name] = validate_bool(raw, field=ENV_PREFIX + name.upper())
            for name in ("bind_host", "capture_backend", "interface", "drop_privileges_user"):
                raw = _env(name)
                if raw is not None:
                    values[name] = raw.strip()
            raw_dir = _env("data_dir")
            if raw_dir:
                values["data_dir"] = Path(raw_dir).expanduser()
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc

        if values.get("capture_backend") not in (None, "auto", "live", "offline", "none"):
            raise ConfigError(
                "capture_backend must be one of: auto, live, offline, none"
            )
        if overrides:
            unknown = set(overrides) - {f.name for f in fields(cls)}
            if unknown:
                raise ConfigError(f"unknown settings: {', '.join(sorted(unknown))}")
            values.update(overrides)
        return cls(**values)
