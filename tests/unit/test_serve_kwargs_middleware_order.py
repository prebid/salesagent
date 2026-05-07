"""Lock the ASGI middleware ordering in ``core.main._serve_kwargs``.

The middleware list is order-sensitive in three places:

1. ``AdminWSGIMount`` MUST run first so admin paths short-circuit to
   Flask without entering buyer-protocol middlewares.
2. ``BearerToAdcpAuthMiddleware`` MUST run before the SDK's
   :class:`BearerTokenAuth` wrap-around so the SDK sees the canonical
   ``x-adcp-auth`` header it's configured to read. (The SDK applies
   bearer auth INSIDE the ``asgi_middleware`` list, so anything in the
   list runs outside the auth wrap.) Reordering breaks A2A buyers
   sending ``Authorization: Bearer`` per RFC 6750.
3. ``SigningVerifyMiddleware`` MUST run last so it only inspects
   buyer-protocol traffic that survived the earlier filters.

If a future contributor reorders the list, this test fails loudly with
the exact reason — protecting properties no unit test of an individual
middleware can catch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.middleware.admin_mount import AdminWSGIMount
from core.middleware.bearer_to_adcp_auth import BearerToAdcpAuthMiddleware
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
    assert (
        middleware_classes[0] is AdminWSGIMount
    ), f"AdminWSGIMount must be first in asgi_middleware; got order: {[c.__name__ for c in middleware_classes]}"


def test_bearer_translation_runs_before_spec_defaults(middleware_classes):
    """``BearerToAdcpAuthMiddleware`` must precede ``SpecDefaultsMiddleware``
    so the SDK auth chain (applied inside the list by serve()) sees the
    canonical ``x-adcp-auth`` header."""
    assert BearerToAdcpAuthMiddleware in middleware_classes, (
        "BearerToAdcpAuthMiddleware missing from asgi_middleware — A2A "
        "buyers sending Authorization: Bearer will get 401 invalid_token."
    )
    assert SpecDefaultsMiddleware in middleware_classes, "SpecDefaultsMiddleware missing from asgi_middleware."
    bearer_idx = middleware_classes.index(BearerToAdcpAuthMiddleware)
    spec_idx = middleware_classes.index(SpecDefaultsMiddleware)
    assert bearer_idx < spec_idx, (
        f"BearerToAdcpAuthMiddleware (idx={bearer_idx}) must run before "
        f"SpecDefaultsMiddleware (idx={spec_idx}). Reordering breaks A2A "
        f"buyers using RFC 6750 Authorization: Bearer headers."
    )


def test_bearer_translation_runs_after_admin_mount(middleware_classes):
    """``BearerToAdcpAuthMiddleware`` must run AFTER ``AdminWSGIMount`` so
    admin paths never enter the bearer translation path."""
    admin_idx = middleware_classes.index(AdminWSGIMount)
    bearer_idx = middleware_classes.index(BearerToAdcpAuthMiddleware)
    assert admin_idx < bearer_idx, (
        f"AdminWSGIMount (idx={admin_idx}) must run before "
        f"BearerToAdcpAuthMiddleware (idx={bearer_idx}) so admin paths "
        f"short-circuit to Flask without entering buyer-protocol middlewares."
    )


def test_signing_verify_runs_last(middleware_classes):
    """``SigningVerifyMiddleware`` must be the last entry — it only
    inspects buyer-protocol traffic that survived the earlier filters."""
    assert middleware_classes[-1] is SigningVerifyMiddleware, (
        f"SigningVerifyMiddleware must be last in asgi_middleware; got "
        f"order: {[c.__name__ for c in middleware_classes]}"
    )
