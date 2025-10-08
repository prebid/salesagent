"""
Unit test specific fixtures.

These fixtures are only available to unit tests.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_all_external_dependencies():
    """Automatically mock all external dependencies for unit tests."""
    # Mock database connections - create a proper context manager mock
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=None)

    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_db.return_value = mock_session

        # Mock external services
        with patch("google.generativeai.configure"):
            with patch("requests.post") as mock_post:
                mock_post.return_value.status_code = 200
                mock_post.return_value.json.return_value = {}

                yield


@pytest.fixture
def isolated_imports():
    """Provide isolated imports for testing."""
    # Store original modules
    original_modules = sys.modules.copy()

    yield

    # Restore original modules
    sys.modules = original_modules


@pytest.fixture
def mock_time():
    """Mock time for deterministic tests."""
    with patch("time.time") as mock_time:
        mock_time.return_value = 1640995200  # 2022-01-01 00:00:00
        with patch("datetime.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value.isoformat.return_value = "2022-01-01T00:00:00"
            mock_datetime.now.return_value.isoformat.return_value = "2022-01-01T00:00:00"
            yield mock_time


@pytest.fixture
def mock_uuid():
    """Mock UUID generation for deterministic tests."""
    with patch("uuid.uuid4") as mock_uuid:
        mock_uuid.return_value.hex = "1234567890abcdef1234567890abcdef"
        yield mock_uuid


@pytest.fixture
def mock_secrets():
    """Mock secrets generation for deterministic tests."""
    with patch("secrets.token_urlsafe") as mock_token:
        mock_token.return_value = "test_token_123456"
        with patch("secrets.token_hex") as mock_hex:
            mock_hex.return_value = "abcdef123456"
            yield mock_token


@pytest.fixture
def fast_password_hashing():
    """Speed up password hashing for tests."""
    with patch("werkzeug.security.generate_password_hash") as mock_hash:
        mock_hash.side_effect = lambda x: f"hashed_{x}"
        with patch("werkzeug.security.check_password_hash") as mock_check:
            mock_check.side_effect = lambda h, p: h == f"hashed_{p}"
            yield
