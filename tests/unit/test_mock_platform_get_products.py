"""Unit test: MockSellerPlatform.get_products delegates to _get_products_impl.

After the platform-delegation refactor (#37) MockSellerPlatform.get_products
is a thin AdCP wire-shape adapter that calls
``src/core/tools/products.py:_get_products_impl`` for the real brief
+ policy + filtering work. These tests verify the delegation contract:

- AccountStore resolves explicit ``"<tenant>:<account>"`` refs.
- get_products builds a ResolvedIdentity from ``ctx`` and forwards to
  the impl.
- The Pydantic ``GetProductsResponse`` is projected to a wire dict.
- Missing tenant metadata raises ACCOUNT_NOT_FOUND before the impl
  runs.

Brief-matching, policy enforcement, and dynamic-product semantics are
tested upstream against ``_get_products_impl`` directly — not here.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adcp.testing import make_request_context

from core.platforms.mock import MockSellerPlatform
from tests.helpers.core_platform import make_active_tenant_session, make_get_products_response


def _make_get_products_response(product_id: str = "prod1"):
    """Build a real GetProductsResponse Pydantic so we exercise
    .model_dump() projection like the production path."""
    return make_get_products_response(product_id)


@pytest.fixture
def active_tenant_session():
    """AccountStore needs a live DB session for the active-tenant
    existence check during ``platform.accounts.resolve``."""
    return make_active_tenant_session()


def test_get_products_delegates_to_impl_and_projects_to_wire(active_tenant_session):
    """MockSellerPlatform.get_products forwards to _get_products_impl
    and projects the response onto the AdCP wire dict shape."""
    response = _make_get_products_response("prod1")

    with (
        patch("core.stores.accounts.get_db_session", return_value=active_tenant_session),
        patch(
            "core.platforms._delegate._get_products_impl",
            new=AsyncMock(return_value=response),
        ) as mock_impl,
        patch(
            "core.platforms._delegate.get_tenant_by_id",
            return_value={"tenant_id": "test-tenant", "name": "Test Tenant"},
        ),
    ):
        platform = MockSellerPlatform()
        account = platform.accounts.resolve(ref={"account_id": "test-tenant:demo"})
        ctx = make_request_context(account=account, request_id="req-1")

        result = asyncio.run(platform.get_products(req={"brief": "test", "buying_mode": "brief"}, ctx=ctx))

    # Wire shape — products[] of dicts, not Pydantic models.
    assert "products" in result
    assert len(result["products"]) == 1
    product = result["products"][0]
    assert isinstance(product, dict)
    assert product["product_id"] == "prod1"
    assert product["delivery_type"] == "non_guaranteed"
    assert product["pricing_options"][0]["pricing_model"] == "cpm"

    # Delegation — impl was called with the buyer's request and a
    # ResolvedIdentity carrying tenant_id from ctx.account.metadata.
    assert mock_impl.await_count == 1
    call_kwargs = mock_impl.await_args
    forwarded_req, forwarded_identity = call_kwargs.args
    assert forwarded_identity.tenant_id == "test-tenant"
    assert forwarded_req.brief == "test"


def test_get_products_rejects_missing_tenant_metadata():
    """If ctx.account has no tenant_id metadata, the delegate raises
    ACCOUNT_NOT_FOUND before reaching the impl. This is a wiring-bug
    guard — auth chain didn't populate the metadata."""
    from adcp.decisioning import AdcpError
    from adcp.decisioning.types import Account

    platform = MockSellerPlatform()
    bad_account = Account(id="anonymous", metadata={})
    ctx = make_request_context(account=bad_account, request_id="req-2")

    with pytest.raises(AdcpError) as exc_info:
        asyncio.run(platform.get_products(req={"brief": "x", "buying_mode": "brief"}, ctx=ctx))

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"


def test_account_store_rejects_unknown_tenant():
    """AccountStore raises ACCOUNT_NOT_FOUND for tenants not in the DB."""
    from adcp.decisioning import AdcpError

    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.scalars.return_value.first.return_value = None  # no row

    with patch("core.stores.accounts.get_db_session", return_value=session):
        platform = MockSellerPlatform()
        with pytest.raises(AdcpError) as exc_info:
            platform.accounts.resolve(ref={"account_id": "nonexistent-tenant:demo"})

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"
