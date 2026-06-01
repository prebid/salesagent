"""Unit tests for ``protocol_webhook_service.get_webhook_service_or_none()``.

Core invariant: shutdown hooks must be able to close the *constructed* webhook
singleton (or no-op if it was never constructed) WITHOUT triggering
construction as a side effect, and the resolution must be location-independent
(no lazy-import tripwire).

``get_webhook_service_or_none()`` is the public, encapsulation-respecting
replacement for ``src/app.py`` reaching into the private module global
``_webhook_service``. It returns the current singleton or ``None`` and — unlike
``get_protocol_webhook_service()`` — never constructs one.

GH #1264 (leak triage fix #3 follow-up)
"""

from __future__ import annotations

import pytest

from src.services import protocol_webhook_service
from src.services.protocol_webhook_service import (
    ProtocolWebhookService,
    get_protocol_webhook_service,
    get_webhook_service_or_none,
)


@pytest.fixture
def reset_webhook_singleton():
    """Snapshot/restore the module-global singleton so tests don't leak state."""
    original = protocol_webhook_service._webhook_service
    try:
        yield
    finally:
        protocol_webhook_service._webhook_service = original


def test_get_webhook_service_or_none_returns_none_when_not_constructed(reset_webhook_singleton):
    """With no singleton constructed, the accessor returns None (not a new instance)."""
    protocol_webhook_service._webhook_service = None

    assert get_webhook_service_or_none() is None


def test_get_webhook_service_or_none_returns_singleton_when_constructed(reset_webhook_singleton):
    """After construction, the accessor returns the exact same singleton instance."""
    protocol_webhook_service._webhook_service = None

    constructed = get_protocol_webhook_service()
    assert isinstance(constructed, ProtocolWebhookService)

    returned = get_webhook_service_or_none()
    assert returned is constructed, "accessor must return the live singleton, not a copy/new instance"


def test_get_webhook_service_or_none_does_not_construct(reset_webhook_singleton):
    """Calling the accessor must NOT construct a singleton as a side effect.

    This is the whole point of the accessor existing separately from
    ``get_protocol_webhook_service()``: a shutdown hook must not resurrect a
    never-used service (and its ``requests.Session`` connection pool) just to
    close it.
    """
    protocol_webhook_service._webhook_service = None

    result = get_webhook_service_or_none()

    assert result is None
    assert protocol_webhook_service._webhook_service is None, (
        "get_webhook_service_or_none() must not bind a new singleton — it must "
        "leave the module global untouched when nothing was constructed."
    )
