"""Unit tests for src.core.http_utils.

Covers:
- parse_bearer_token: contract for all four Bearer-parsing call sites
  (auth.py, auth_middleware.py, resolved_identity.py, routes/tmp_providers.py).

The function changed semantics relative to the three old inline parsers:
  - ``split(None, 1)`` accepts tab/multi-space separators the old parsers rejected.
  - Case-insensitive scheme check (RFC 7235 §2.1) — ``bearer`` is accepted.
  - Scheme-less values (no space) return None.
  - Empty token after scheme returns None.

This table test pins the contract so that deleting the scheme check or
changing the split behaviour fails immediately.
"""

from __future__ import annotations

import pytest

from src.core.http_utils import parse_bearer_token


@pytest.mark.parametrize(
    "header, expected",
    [
        # Standard form
        ("Bearer abc123", "abc123"),
        # Case-insensitive scheme (RFC 7235 §2.1)
        ("bearer abc123", "abc123"),
        ("BEARER abc123", "abc123"),
        # Deliberate delta: split(None, 1) accepts tab/multi-space separators
        # that the three old inline parsers rejected.
        ("Bearer\tabc123", "abc123"),
        ("Bearer  abc123", "abc123"),
        # Scheme-less value — must return None (not silently strip)
        ("abc123", None),
        # Empty token after scheme — must return None
        ("Bearer ", None),
        ("Bearer\t", None),
        # Completely empty string
        ("", None),
        # Whitespace only
        ("   ", None),
        # Wrong scheme
        ("Basic abc123", None),
        ("Token abc123", None),
    ],
)
def test_parse_bearer_token(header: str, expected: str | None) -> None:
    """parse_bearer_token returns the token or None for each input shape."""
    assert parse_bearer_token(header) == expected


def test_parse_bearer_token_strips_leading_trailing_whitespace_from_header() -> None:
    """Leading/trailing whitespace on the full header value is stripped before parsing."""
    assert parse_bearer_token("  Bearer mytoken  ") == "mytoken"


def test_parse_bearer_token_returns_none_for_bearer_with_only_spaces_after() -> None:
    """'Bearer   ' (spaces only after scheme) returns None — empty token."""
    assert parse_bearer_token("Bearer   ") is None
