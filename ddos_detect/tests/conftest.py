"""Shared fixtures. Everything runs against a temporary data directory."""

from __future__ import annotations

import pytest

from ddos_detect.app import Application
from ddos_detect.config import Settings

ADMIN_PASSWORD = "correct-horse-battery-staple-42"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        bind_port=0,
        # Keep the KDF cheap in tests; production uses the configured default.
        kdf_iterations=1000,
        learning_seconds=2,
        window_seconds=10,
    )


@pytest.fixture
def app(settings) -> Application:
    application = Application.build(settings)
    yield application
    application.close()


@pytest.fixture
def admin(app) -> str:
    app.auth.create_user("admin", ADMIN_PASSWORD, "admin", actor="test")
    return "admin"
