"""Lock the ASGI middleware ordering in ``core.main._serve_kwargs``.

The middleware list is order-sensitive in two places:

1. ``AdminWSGIMount`` MUST run first so admin paths short-circuit to
   Flask without entering buyer-protocol middlewares.
2. ``SigningVerifyMiddleware`` MUST run last so it only inspects
   buyer-protocol traffic that survived the earlier filters.

If a future contributor reorders the list, this test fails loudly with
the exact reason — protecting properties no unit test of an individual
middleware can catch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.middleware.admin_mount import AdminWSGIMount
from core.middleware.agent_card_public_url import AgentCardPublicUrlMiddleware
from core.middleware.spec_defaults import SpecDefaultsMiddleware
from src.core.signing import SigningVerifyMiddleware


@pytest.fixture
def middleware_classes() -> list[type]:
    """Extract just the middleware *classes* from the asgi_middleware tuples.

    ``_serve_kwargs`` triggers ``build_router`` → DB query for active
    tenants. We bypass that with a lightweight stub: the asgi_middleware
    list construction is deterministic and doesn't depend on what the
    router or admin app look like.
    """
    from core import main as core_main

    with (
        patch.object(core_main, "build_router", return_value=MagicMock()),
        patch("src.admin.app.create_app", return_value=MagicMock()),
        patch("core.main.build_subdomain_router", return_value=MagicMock()),
    ):
        kwargs = core_main._serve_kwargs(include_scheduler=False, include_subdomain_routing=True)
    return [entry[0] for entry in kwargs["asgi_middleware"]]


def test_admin_wsgi_mount_runs_first(middleware_classes):
    """Admin paths must short-circuit to Flask before any buyer-protocol
    middleware sees them."""
    assert middleware_classes[0] is AdminWSGIMount, (
        f"AdminWSGIMount must be first in asgi_middleware; got order: {[c.__name__ for c in middleware_classes]}"
    )


def test_spec_defaults_present(middleware_classes):
    """``SpecDefaultsMiddleware`` must be in the chain — it backfills
    pre-v3 wire defaults outside the SDK validation boundary."""
    assert SpecDefaultsMiddleware in middleware_classes, (
        "SpecDefaultsMiddleware missing from asgi_middleware — pre-v3 clients will fail strict-mode validation."
    )


def test_agent_card_public_url_middleware_present(middleware_classes):
    """``AgentCardPublicUrlMiddleware`` must be in the chain — without it,
    ``/.well-known/agent-card.json`` advertises the container's localhost
    URL and SDK clients can't discover the public A2A endpoint (#103)."""
    assert AgentCardPublicUrlMiddleware in middleware_classes, (
        "AgentCardPublicUrlMiddleware missing from asgi_middleware — A2A "
        "agent card will leak the localhost URL and SDK discovery breaks."
    )


def test_signing_verify_runs_last(middleware_classes):
    """``SigningVerifyMiddleware`` must be the last entry — it only
    inspects buyer-protocol traffic that survived the earlier filters."""
    assert middleware_classes[-1] is SigningVerifyMiddleware, (
        f"SigningVerifyMiddleware must be last in asgi_middleware; got "
        f"order: {[c.__name__ for c in middleware_classes]}"
    )
