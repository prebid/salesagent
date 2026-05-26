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

import os
from unittest.mock import MagicMock, patch

import pytest

from core.middleware.admin_mount import AdminWSGIMount
from core.middleware.origin_guard import BuyerProtocolOriginGuardMiddleware
from src.core.middleware.tracing import TracingMiddleware
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


def test_tracing_middleware_runs_outermost(middleware_classes):
    """TracingMiddleware must be the outermost wrapper so every request,
    including admin paths, gets a trace span."""
    assert middleware_classes[0] is TracingMiddleware, (
        f"TracingMiddleware must be first in asgi_middleware; got order: {[c.__name__ for c in middleware_classes]}"
    )


def test_admin_wsgi_mount_runs_before_buyer_protocol(middleware_classes):
    """Admin paths must short-circuit to Flask before buyer-protocol
    middlewares see them."""
    tracing_index = middleware_classes.index(TracingMiddleware)
    admin_index = middleware_classes.index(AdminWSGIMount)
    assert admin_index == tracing_index + 1, (
        f"AdminWSGIMount must immediately follow TracingMiddleware; got order: {[c.__name__ for c in middleware_classes]}"
    )


def test_public_url_resolver_is_callable():
    """The agent-card public URL must be a per-request callable so
    multi-tenant subdomain deploys advertise the right URL per request
    (#103). The salesagent middleware-based rewrite was retired in favor
    of the SDK's native ``serve(public_url=callable)`` after adcp 5.3.0
    #680 fixed the ``transport='both'`` composed-lifespan crash.

    The behavior of the resolver itself is covered in
    ``test_agent_card_public_url_middleware.py``; this test only pins
    that ``_serve_kwargs`` keeps the resolver wired."""
    from core import main as core_main

    with (
        patch.object(core_main, "build_router", return_value=MagicMock()),
        patch("src.admin.app.create_app", return_value=MagicMock()),
        patch("core.main.build_subdomain_router", return_value=MagicMock()),
    ):
        kwargs = core_main._serve_kwargs(include_scheduler=False, include_subdomain_routing=True)
    public_url = kwargs.get("public_url")
    assert callable(public_url), (
        f"public_url must be a per-request callable (PublicUrlResolver), got {type(public_url).__name__!r}. "
        "Static strings can't carry X-Forwarded-Host for multi-tenant subdomain deploys."
    )


def test_buyer_protocol_origin_guard_wired_before_signing(middleware_classes):
    """Routed deployments disable FastMCP Host validation, but must keep
    Origin validation before requests enter buyer-protocol handlers."""
    guard_index = middleware_classes.index(BuyerProtocolOriginGuardMiddleware)
    signing_index = middleware_classes.index(SigningVerifyMiddleware)
    assert middleware_classes[guard_index] is BuyerProtocolOriginGuardMiddleware
    assert guard_index < signing_index


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


def test_pre_validation_hooks_do_not_default_account_refs():
    """Account-bearing tools must not get seller-specific placeholder refs.

    Missing ``account`` is a buyer protocol violation for these tools; the
    typed dispatcher should reject it instead of letting a pre-validation hook
    inject a fake ``account_id`` such as the old ``auth-chain`` sentinel.
    """
    from core import main as core_main

    with (
        patch.object(core_main, "build_router", return_value=MagicMock()),
        patch("src.admin.app.create_app", return_value=MagicMock()),
        patch("core.main.build_subdomain_router", return_value=MagicMock()),
    ):
        kwargs = core_main._serve_kwargs(include_scheduler=False, include_subdomain_routing=True)
    hooks = kwargs.get("pre_validation_hooks")
    assert hooks is not None, "pre_validation_hooks missing — pre-v3 buyer payloads will fail validation"

    account_required_tools = {"sync_accounts", "activate_signal"}
    unexpected_hooks = sorted(account_required_tools.intersection(hooks))
    assert not unexpected_hooks, (
        f"pre_validation_hooks must not mask missing account refs with seller-specific defaults: {unexpected_hooks}"
    )
    create_hook = hooks.get("create_media_buy")
    assert create_hook is not None, "create_media_buy needs strict dev-mode unknown-field rejection"

    payload = {"brand": {"domain": "example.com"}}
    normalized = create_hook("create_media_buy", payload)
    assert "account" not in normalized
    assert "account_id" not in normalized

    with pytest.raises(ValueError, match="campaign_ref"):
        create_hook("create_media_buy", {"campaign_ref": "old-ref"})


def test_signing_verify_runs_last(middleware_classes):
    """``SigningVerifyMiddleware`` must be the last entry — it only
    inspects buyer-protocol traffic that survived the earlier filters."""
    assert middleware_classes[-1] is SigningVerifyMiddleware, (
        f"SigningVerifyMiddleware must be last in asgi_middleware; got "
        f"order: {[c.__name__ for c in middleware_classes]}"
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


def _kwargs_with_dns_rebinding_env(value: str | None, *, include_subdomain_routing: bool) -> dict:
    """Build ``_serve_kwargs`` with isolated DNS-rebinding env state."""
    from core import main as core_main

    with (
        patch.object(core_main, "build_router", return_value=MagicMock()),
        patch("src.admin.app.create_app", return_value=MagicMock()),
        patch("core.main.build_subdomain_router", return_value=MagicMock()),
        patch.dict(os.environ, {}, clear=False),
    ):
        if value is None:
            os.environ.pop("ADCP_DNS_REBINDING_PROTECTION", None)
        else:
            os.environ["ADCP_DNS_REBINDING_PROTECTION"] = value
        return core_main._serve_kwargs(
            include_scheduler=False,
            include_subdomain_routing=include_subdomain_routing,
        )


def test_dns_rebinding_defaults_off_when_subdomain_router_validates_hosts():
    """Dynamic tenant hosts cannot be represented in FastMCP's exact
    allowlist, so routed production lets SubdomainTenantMiddleware own Host
    validation by default."""
    kwargs = _kwargs_with_dns_rebinding_env(None, include_subdomain_routing=True)
    assert kwargs["enable_dns_rebinding_protection"] is False


def test_dns_rebinding_defaults_on_without_subdomain_router():
    """Non-routed setups keep FastMCP's DNS-rebinding check by default."""
    kwargs = _kwargs_with_dns_rebinding_env(None, include_subdomain_routing=False)
    assert kwargs["enable_dns_rebinding_protection"] is True


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
def test_dns_rebinding_env_override_wins(value, expected):
    """Operators can still force FastMCP host validation either way."""
    kwargs = _kwargs_with_dns_rebinding_env(value, include_subdomain_routing=True)
    assert kwargs["enable_dns_rebinding_protection"] is expected


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


def test_auth_optional_tools_known_to_sdk_validator():
    """Every entry in ``AUTH_OPTIONAL_TOOLS`` must be in the SDK's
    ``ADCP_TOOL_DEFINITIONS``. The same set is passed to
    ``BearerTokenAuth.mcp_discovery_tools``, which runs
    ``validate_discovery_set`` at construction — a name the SDK doesn't
    know about would crash the server at boot. Catching it here makes
    the failure visible in unit tests instead.
    """
    from adcp.server.mcp_tools import ADCP_TOOL_DEFINITIONS

    from core.main import AUTH_OPTIONAL_TOOLS

    sdk_known_names = {t["name"] for t in ADCP_TOOL_DEFINITIONS}
    unknown = AUTH_OPTIONAL_TOOLS - sdk_known_names
    assert not unknown, (
        f"AUTH_OPTIONAL_TOOLS contains tools the SDK doesn't know about — "
        f"BearerTokenAuth will reject them at construction: {sorted(unknown)}"
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
