"""Exception types shared across the package."""


class DdosDetectError(Exception):
    """Base class for all errors raised by this package."""


class ValidationError(DdosDetectError):
    """Caller-supplied input failed validation. Safe to surface to the user."""


class AuthorizationError(DdosDetectError):
    """The requested target is not covered by the authorization ledger."""


class AuthenticationError(DdosDetectError):
    """Credentials or session were missing, expired, or invalid."""


class RateLimitError(DdosDetectError):
    """Caller exceeded the configured request budget."""

    def __init__(self, message: str, retry_after: float = 1.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class CaptureError(DdosDetectError):
    """The packet capture backend could not be started or failed while running."""


class ConfigError(DdosDetectError):
    """Configuration is missing or internally inconsistent."""
