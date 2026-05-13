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


def test_agent_card_public_url_middleware_present(middleware_classes):
    """``AgentCardPublicUrlMiddleware`` must be in the chain — without it,
    ``/.well-known/agent-card.json`` advertises the container's localhost
    URL and SDK clients can't discover the public A2A endpoint (#103).

    The 5.1 callable ``public_url`` resolver (#650) is the eventual
    replacement, but ``transport="both"`` in 5.2.0 has a bug where a
    callable ``public_url`` breaks ``_composed_lifespan`` with
    ``AttributeError: 'function' object has no attribute 'router'``."""
    assert AgentCardPublicUrlMiddleware in middleware_classes, (
        "AgentCardPublicUrlMiddleware missing from asgi_middleware — A2A "
        "agent card will leak the localhost URL and SDK discovery breaks."
    )


def test_pre_validation_hooks_wired():
    """Heuristic backfills for pre-v3 / pre-4.4 buyers must stay wired.

    The hook backfills ``get_products.buying_mode='brief'`` and infers
    ``sync_creatives`` ``asset_type`` discriminators for buyers omitting
    those fields. Removing it breaks tag-less buyers and our own
    integration tests that send minimal requests. adcp 5.2 deprecated
    the public ``spec_compat_hooks()`` symbol (#667) in favour of typed
    AdapterPair adapters that only fire for buyers declaring
    ``adcp_version='2.5'`` — but our test buyers don't declare a version,
    so the unconditional hooks remain load-bearing. Use the private
    ``_spec_compat_hooks_impl`` (same as SDK's own tests) to avoid the
    DeprecationWarning."""
    from unittest.mock import patch

    from core import main as core_main

    with (
        patch.object(core_main, "build_router", return_value=MagicMock()),
        patch("src.admin.app.create_app", return_value=MagicMock()),
        patch("core.main.build_subdomain_router", return_value=MagicMock()),
    ):
        kwargs = core_main._serve_kwargs(include_scheduler=False, include_subdomain_routing=True)
    hooks = kwargs.get("pre_validation_hooks")
    assert hooks is not None, "pre_validation_hooks missing — pre-v3 buyer payloads will fail validation"
    assert "get_products" in hooks
    assert "sync_creatives" in hooks


def test_signing_verify_runs_last(middleware_classes):
    """``SigningVerifyMiddleware`` must be the last entry — it only
    inspects buyer-protocol traffic that survived the earlier filters."""
    assert middleware_classes[-1] is SigningVerifyMiddleware, (
        f"SigningVerifyMiddleware must be last in asgi_middleware; got "
        f"order: {[c.__name__ for c in middleware_classes]}"
    )


def test_www_authenticate_runs_after_admin_mount_and_before_signing(middleware_classes):
    """``WWWAuthenticateMiddleware`` must sit AFTER ``AdminWSGIMount`` and
    BEFORE ``SigningVerifyMiddleware``.

    AFTER ``AdminWSGIMount``: admin Google-OAuth-gated paths short-circuit
    to Flask before WWWAuthenticate sees them — a Bearer-scheme challenge
    is contextually wrong for those.

    BEFORE ``SigningVerifyMiddleware`` (and every other buyer-protocol
    middleware that can emit 401): WWWAuthenticate must be OUTSIDE the
    middlewares that emit 401 to wrap their response and inject the
    header if missing.

    A future refactor that moves it ahead of ``AdminWSGIMount`` would put
    a misleading Bearer challenge on Google-OAuth 401s; moving it inside
    ``SigningVerifyMiddleware`` would mean signing 401s ship without the
    header. Pin the position so either drift surfaces here.
    Workaround tracker: adcp-client-python#712.
    """
    from core.middleware.www_authenticate import WWWAuthenticateMiddleware

    assert WWWAuthenticateMiddleware in middleware_classes, (
        f"WWWAuthenticateMiddleware missing from asgi_middleware — RFC 6750 §3 "
        f"compliance breaks. Got order: {[c.__name__ for c in middleware_classes]}"
    )
    admin_idx = middleware_classes.index(AdminWSGIMount)
    www_idx = middleware_classes.index(WWWAuthenticateMiddleware)
    signing_idx = middleware_classes.index(SigningVerifyMiddleware)
    assert admin_idx < www_idx < signing_idx, (
        f"WWWAuthenticateMiddleware must run AFTER AdminWSGIMount and BEFORE "
        f"SigningVerifyMiddleware. Got admin={admin_idx}, www={www_idx}, "
        f"signing={signing_idx}; full order: {[c.__name__ for c in middleware_classes]}"
    )


def _kwargs_with(env: dict[str, str]) -> dict:
    """Build ``_serve_kwargs`` output with the given env overrides applied."""
    from core import main as core_main

    with (
        patch.object(core_main, "build_router", return_value=MagicMock()),
        patch("src.admin.app.create_app", return_value=MagicMock()),
        patch("core.main.build_subdomain_router", return_value=MagicMock()),
        patch.dict("os.environ", env, clear=False),
    ):
        return core_main._serve_kwargs(include_scheduler=False, include_subdomain_routing=True)


@pytest.mark.parametrize("value", ["true", "TRUE", "True"])
def test_stateless_http_env_var_enables_stateless_mode(value):
    """``ADCP_STATELESS_HTTP`` flips the MCP transport to stateless mode.

    Required on multi-replica deployments without sticky LB routing on
    ``Mcp-Session-Id``: each replica owns its own in-memory
    ``_server_instances`` dict, so a session created on Instance A can't
    be looked up on Instance B and ``tools/list`` randomly 404s after a
    successful ``initialize`` lands elsewhere.

    ``FASTMCP_STATELESS_HTTP`` alone is insufficient — the adcp wrapper
    overrides FastMCP's env-var read by assigning
    ``mcp.settings.stateless_http`` from this kwarg unconditionally.
    """
    kwargs = _kwargs_with({"ADCP_STATELESS_HTTP": value})
    assert kwargs["stateless_http"] is True, (
        f"ADCP_STATELESS_HTTP={value!r} must produce stateless_http=True; got {kwargs.get('stateless_http')!r}"
    )


@pytest.mark.parametrize("value", ["false", "0", "", "no", "anything-else"])
def test_stateless_http_defaults_off(value):
    """Single-replica dev / test / single-pod prod gets stateful sessions
    by default — preserves the session-reuse perf for chatty workloads
    (compliance sweeps, BDD, local dev) and matches the upstream
    FastMCP default."""
    kwargs = _kwargs_with({"ADCP_STATELESS_HTTP": value})
    assert kwargs["stateless_http"] is False, (
        f"ADCP_STATELESS_HTTP={value!r} must leave stateless_http=False; got {kwargs.get('stateless_http')!r}"
    )


def test_stateless_http_unset_is_stateful():
    """Unset env var must yield stateful mode — no surprise behavior on
    deployments that haven't opted in."""
    import os as _os

    saved = _os.environ.pop("ADCP_STATELESS_HTTP", None)
    try:
        kwargs = _kwargs_with({})
        assert kwargs["stateless_http"] is False
    finally:
        if saved is not None:
            _os.environ["ADCP_STATELESS_HTTP"] = saved
