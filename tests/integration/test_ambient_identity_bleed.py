"""Regression test for salesagent-tb8c: ambient FastMCP context bleeds into
explicit identity=None on *_raw transport wrappers.

Root cause (see salesagent-tb8c design notes):
    `identity: ResolvedIdentity | None = None` cannot distinguish "caller
    explicitly passed identity=None" (meaning: exercise the anonymous/
    no-tenant path) from "caller omitted the argument" (meaning: please
    resolve identity from ambient context). Every `*_raw` wrapper treats
    `if identity is None:` as the latter, silently re-resolving via
    `resolve_identity_from_context()` -> `get_http_headers()`, which reads
    FastMCP's `_current_http_request` ContextVar UNCONDITIONALLY -- i.e.
    whenever a real ambient FastMCP HTTP request is active in the current
    async task, an explicit `identity=None` gets upgraded to a real tenant.

This test reproduces the actual trigger condition that the existing unit
test (tests/unit/test_products_transport_wrappers.py::
test_a2a_passes_none_identity_when_not_provided) does NOT cover: an ambient
FastMCP HTTP request context active in the current task when the wrapper is
invoked. It uses FastMCP's own `set_http_request()` context manager (the
same one FastMCP's ASGI middleware uses per-request) with a `Host: localhost`
header, which is the exact fallback documented in
src/core/resolved_identity.py:118-124 that lands on tenant "default".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.http import set_http_request
from sqlalchemy.orm import Session as SASession
from starlette.requests import Request as StarletteRequest

from src.core.database.database_session import get_engine
from src.core.schemas import GetProductsResponse
from src.core.schemas.account import ListAccountsResponse, SyncAccountsResponse

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def bound_session(integration_db):
    """A session bound to the test DB with all factories registered to it.

    Mirrors tests/integration/test_delivery_simulation_config.py's
    `bound_session` fixture — lets factories run outside the full
    IntegrationEnv harness for a test that only needs a bare tenant row.
    """
    from tests.factories import ALL_FACTORIES

    engine = get_engine()
    session = SASession(bind=engine)
    previous = [f._meta.sqlalchemy_session for f in ALL_FACTORIES]
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = session
    try:
        yield session
    finally:
        for f, prev in zip(ALL_FACTORIES, previous, strict=True):
            f._meta.sqlalchemy_session = prev
        session.rollback()
        session.close()


def _localhost_request() -> StarletteRequest:
    """Build a real Starlette Request with a `Host: localhost` header.

    This mirrors the ASGI scope FastMCP's RequestContextMiddleware stores in
    `_current_http_request` for the duration of a real HTTP request -- the
    exact ambient context `get_http_headers()` reads.
    """
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"host", b"localhost")],
        "client": None,
        "server": None,
        "scheme": "http",
        "root_path": "",
    }
    return StarletteRequest(scope)


class TestAmbientContextIdentityBleed:
    """Covers salesagent-tb8c: explicit identity=None must stay anonymous."""

    async def test_explicit_none_identity_survives_ambient_http_context(self, bound_session):
        """An explicit identity=None must NOT be upgraded to a real tenant
        just because an ambient FastMCP HTTP request is active.

        Setup: a "default" tenant exists (so the localhost->default fallback
        in resolved_identity.py has something to resolve to) and a real
        FastMCP HTTP request context is active with Host: localhost -- the
        exact condition under which src/core/transport_helpers.py's
        resolve_identity_from_context() would land on tenant "default".

        The A2A/REST raw wrapper (get_products_raw) is called with an
        EXPLICIT identity=None, which per the wrapper's contract should mean
        "exercise the anonymous/no-tenant path" (e.g. the A2A server calling
        core_get_products_tool with identity=None by design, per
        src/a2a_server/adcp_a2a_server.py:1500-1520). The current buggy
        `if identity is None:` guard cannot distinguish this from "omitted",
        so it re-resolves via ambient context and upgrades identity to the
        "default" tenant -- exactly the bug this test demonstrates.
        """
        from tests.factories import TenantFactory

        TenantFactory(tenant_id="default_tenant", subdomain="default")
        bound_session.commit()

        with (
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=GetProductsResponse(products=[]),
            ) as mock_impl,
            set_http_request(_localhost_request()),
        ):
            from src.core.tools.products import get_products_raw

            await get_products_raw(brief="test", ctx=None, identity=None)

        _, identity_used = mock_impl.call_args.args
        assert identity_used is None, (
            "explicit identity=None must remain anonymous even with an ambient "
            "FastMCP HTTP request active (Host: localhost); instead it was "
            f"silently re-resolved to {identity_used!r} "
            f"(tenant_id={getattr(identity_used, 'tenant_id', None)!r}) via "
            "resolve_identity_from_context()'s unconditional get_http_headers() "
            "fallback — this is the ambient-context bleed bug (salesagent-tb8c)"
        )

    @pytest.mark.parametrize(
        "impl_patch_target,impl_return,wrapper_is_async",
        [
            pytest.param(
                "src.core.tools.products._get_products_impl",
                GetProductsResponse(products=[]),
                True,
                id="get_products_raw-require_valid_token_False",
            ),
            pytest.param(
                # accounts.py:230 — require_valid_token=False call-site variant.
                "src.core.tools.accounts._list_accounts_impl",
                ListAccountsResponse(accounts=[]),
                False,
                id="list_accounts_raw-require_valid_token_False",
            ),
            pytest.param(
                # accounts.py:754 — require_valid_token=True call-site variant,
                # per architect review (salesagent-tb8c): the two accounts.py
                # sites deliberately differ on require_valid_token and must
                # both be covered, not just get_products_raw.
                "src.core.tools.accounts._sync_accounts_impl",
                SyncAccountsResponse(accounts=[]),
                True,
                id="sync_accounts_raw-require_valid_token_True",
            ),
        ],
    )
    async def test_explicit_none_identity_survives_ambient_http_context_parametrized(
        self,
        bound_session,
        impl_patch_target,
        impl_return,
        wrapper_is_async,
    ):
        """Parametrized coverage across representative *_raw sites (salesagent-tb8c).

        The architect review flagged that a single get_products_raw reproduction
        does not prove the other 14 sites are migrated, and that require_valid_token
        genuinely varies across call sites (accounts.py:230 is False, accounts.py:754
        is True) — the shared helper must parameterize both correctly. This covers
        one require_valid_token=False site (list_accounts_raw), one
        require_valid_token=True site (sync_accounts_raw), plus the original
        get_products_raw reproduction, all under the same ambient-HTTP-context
        trigger condition.
        """
        from tests.factories import TenantFactory

        TenantFactory(tenant_id="default_tenant", subdomain="default")
        bound_session.commit()

        impl_new_callable = AsyncMock if wrapper_is_async else MagicMock
        with (
            patch(impl_patch_target, new_callable=impl_new_callable, return_value=impl_return) as mock_impl,
            set_http_request(_localhost_request()),
        ):
            if impl_patch_target.endswith("_get_products_impl"):
                from src.core.tools.products import get_products_raw

                await get_products_raw(brief="test", ctx=None, identity=None)
            elif impl_patch_target.endswith("_list_accounts_impl"):
                from src.core.tools.accounts import list_accounts_raw

                list_accounts_raw(ctx=None, identity=None)
            elif impl_patch_target.endswith("_sync_accounts_impl"):
                from src.core.tools.accounts import sync_accounts_raw

                await sync_accounts_raw(ctx=None, identity=None)
            else:  # pragma: no cover — defensive, keeps parametrize additions honest
                raise AssertionError(f"no dispatch wired for {impl_patch_target}")

        _, identity_used = mock_impl.call_args.args
        assert identity_used is None, (
            f"[{impl_patch_target}] explicit identity=None must remain anonymous "
            "even with an ambient FastMCP HTTP request active (Host: localhost); "
            f"instead it was silently re-resolved to {identity_used!r} "
            f"(tenant_id={getattr(identity_used, 'tenant_id', None)!r}) — "
            "the ambient-context bleed bug (salesagent-tb8c) at this call site"
        )
