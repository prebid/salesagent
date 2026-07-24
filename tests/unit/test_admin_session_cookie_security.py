"""Security invariants for admin session cookies."""

from unittest.mock import patch

import pytest

from src.admin.app import create_app


@pytest.mark.parametrize("production", [False, True])
def test_admin_session_cookie_is_always_httponly(production: bool) -> None:
    """JavaScript never needs access to the bearer-equivalent session cookie."""
    with (
        patch("src.admin.app.is_admin_production", return_value=production),
        patch("src.admin.app.is_single_tenant_mode", return_value=True),
    ):
        app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})

    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
