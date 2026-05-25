"""Unit test: get_products dispatched via the framework's PlatformHandler.

After the platform-delegation refactor (#37) the dispatch path is:

  GetProductsRequest (typed Pydantic, wire shape)
      ↓
  PlatformHandler.get_products()             — framework's dispatch shim
      ↓
  MockSellerPlatform.get_products()          — thin AdCP adapter
      ↓
  _delegate_get_products()                   — builds ResolvedIdentity
      ↓
  src/core/tools/products.py:_get_products_impl  — REAL business logic (mocked here)
      ↓
  GetProductsResponse → dict                 — wire-compatible result

This test exercises the framework's real dispatch machinery + the new
delegation glue. ``_get_products_impl`` itself is mocked — its
brief-matching, policy enforcement, and dynamic-product semantics
are tested upstream against the impl directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from adcp.decisioning.serve import create_adcp_server_from_platform
from adcp.server.base import ToolContext
from adcp.types import GetProductsRequest

from core.platforms.mock import MockSellerPlatform
from tests.helpers.core_platform import make_active_tenant_session, make_get_products_response


def _impl_response_with_one_product(product_id: str = "demo-product"):
    return make_get_products_response(product_id, name="Demo Product", description="A demo product")


@pytest.fixture
def mocked_pipeline():
    """Mock the AccountStore session AND the upstream _get_products_impl."""
    impl_mock = AsyncMock(return_value=_impl_response_with_one_product())

    with (
        patch("core.stores.accounts.get_db_session", return_value=make_active_tenant_session()),
        patch("core.platforms._delegate._get_products_impl", new=impl_mock),
        patch(
            "core.platforms._delegate.get_tenant_by_id",
            return_value={"tenant_id": "demo-tenant", "name": "Demo Tenant"},
        ),
    ):
        yield impl_mock


def test_get_products_dispatches_through_framework_handler(mocked_pipeline):
    """A real PlatformHandler dispatch reaches the delegate, which
    forwards to the impl and projects the response onto a wire dict."""
    platform = MockSellerPlatform()
    handler, _executor, _registry = create_adcp_server_from_platform(platform, auto_emit_completion_webhooks=False)

    req = GetProductsRequest(
        account={"account_id": "demo-tenant:demo"},
        promoted_offering="shoes",
        buying_mode="brief",
        brief="display ads for sneakers",
    )
    ctx = ToolContext()

    result = asyncio.run(handler.get_products(req, ctx))

    assert isinstance(result, dict)
    assert "products" in result
    assert len(result["products"]) == 1

    product = result["products"][0]
    assert product["product_id"] == "demo-product"
    assert product["delivery_type"] == "non_guaranteed"
    assert product["pricing_options"][0]["pricing_model"] == "cpm"
    # Framework requires reporting_capabilities on every product
    assert "reporting_capabilities" in product
    # Wire shape: format_ids must be FormatId-shaped dicts
    assert product["format_ids"][0]["agent_url"].startswith("https://")

    # Delegation reached the impl with the buyer's brief intact.
    assert mocked_pipeline.await_count == 1
    forwarded_req, forwarded_identity = mocked_pipeline.await_args.args
    assert forwarded_req.brief == "display ads for sneakers"
    assert forwarded_identity.tenant_id == "demo-tenant"
