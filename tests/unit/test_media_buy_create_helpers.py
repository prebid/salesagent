"""Unit tests for media_buy_create helper functions.

Tests the helper functions used in media buy creation, particularly
format specification retrieval: the error taxonomy of the single shared
fetch path (format_resolver.fetch_format_spec) as seen through the
``_get_format_spec_sync`` delegate — typed AdCPError propagates with its
recovery semantics, untyped failures and unknown formats become None.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.tools.media_buy_create import _get_format_spec_sync

_AGENT_URL = "https://creative.adcontextprotocol.org"


@contextmanager
def _patched_registry(**get_format_kwargs):
    """Patch the creative-agent registry with a stub ``get_format``.

    Single stubbing seam for every test in this module (the same target the
    harness envs patch as ``EXTERNAL_PATCHES['registry']``); pass AsyncMock
    kwargs (``return_value=`` / ``side_effect=``) for the stubbed coroutine.
    """
    mock_registry = MagicMock()
    mock_registry.get_format = AsyncMock(**get_format_kwargs)
    with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
        yield mock_registry


class TestGetFormatSpecSync:
    """Test synchronous format specification retrieval."""

    def test_successful_format_retrieval(self):
        """Test successful format spec retrieval with mocked registry."""
        mock_format_spec = MagicMock()
        mock_format_spec.format_id.id = "display_300x250_image"
        mock_format_spec.name = "Medium Rectangle - Image"

        with _patched_registry(return_value=mock_format_spec):
            format_spec = _get_format_spec_sync(_AGENT_URL, "display_300x250_image")
            assert format_spec is not None
            assert format_spec.format_id.id == "display_300x250_image"
            assert format_spec.name == "Medium Rectangle - Image"

    def test_unknown_format_returns_none(self):
        """A genuinely unknown format (registry returns None) stays None —
        the correctable CREATIVE_REJECTED path downstream is correct for
        that case (unlike typed transient errors, which must propagate).
        """
        with _patched_registry(return_value=None):
            assert _get_format_spec_sync(_AGENT_URL, "unknown_format_xyz") is None

    def test_untyped_exception_returns_none(self):
        """Untyped failures are caught and become None (unknown-format path)."""
        with _patched_registry(side_effect=Exception("Network error")):
            assert _get_format_spec_sync(_AGENT_URL, "display_300x250_image") is None


class TestFormatSpecTransientErrors:
    """Transient creative-agent failures must stay transient on the wire.

    _get_format_spec_sync wraps the async registry; a typed transient
    AdCPError from it (rate limit, timeout, connect failure) must PROPAGATE
    so the buyer sees SERVICE_UNAVAILABLE (transient, retryable) — not be
    swallowed into None, which downstream validation treats as unknown format
    and converts to a correctable CREATIVE_REJECTED that misdirects the buyer
    into "fixing" a fine creative (PR #1430 review).
    """

    def test_transient_registry_error_propagates_not_rejected(self):
        from src.core.exceptions import AdCPServiceUnavailableError

        with _patched_registry(side_effect=AdCPServiceUnavailableError("Creative agent rate limited (429)")):
            with pytest.raises(AdCPServiceUnavailableError):
                _get_format_spec_sync(_AGENT_URL, "display_300x250_image")
