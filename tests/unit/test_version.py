"""Tests for the version module."""

import re

from src.core.version import get_version


def test_get_version_returns_valid_semver():
    """get_version should return a valid semantic version string."""
    version = get_version()

    # Should be a string
    assert isinstance(version, str)

    # Should match semver pattern (e.g., "1.2.0" or "0.0.0")
    assert re.match(r"^\d+\.\d+\.\d+", version), f"Version '{version}' doesn't match semver pattern"


def test_get_version_not_default():
    """get_version should return the actual version, not the fallback."""
    version = get_version()

    # Verifies that at least one retrieval method works (importlib.metadata or pyproject.toml).
    # The fallback "0.0.0" is only returned if both methods fail, which would indicate
    # a broken environment rather than an actual version of 0.0.0.
    assert version != "0.0.0", "Version should not be the fallback value"
