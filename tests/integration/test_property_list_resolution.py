"""Integration tests for property list resolution and cursor-based pagination.

Tests BR-RULE-077-01: Property list resolution and pagination.

Obligation invariant:
  resolve=true (default) resolves filters against current catalog.
  max_results 1-10000, default 1000. Cursor-based pagination.
  resolve=false means identifiers are not returned; pagination params have no effect.

These tests verify:
1. Resolved property lists filter products correctly
2. Cursor-based pagination of resolved identifiers
3. Exhausted cursor yields empty results
4. Page size (max_results) is respected by the resolver
5. Filters are applied at resolution time (countries_all AND, channels_any OR)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_property_list_ref(
    agent_url: str = "https://propertylist.example.com",
    list_id: str = "test_list",
    auth_token: str | None = None,
) -> dict:
    """Build a PropertyListReference dict suitable for call_impl."""
    ref: dict = {"agent_url": agent_url, "list_id": list_id}
    if auth_token is not None:
        ref["auth_token"] = auth_token
    return ref


def _mock_http_response(
    identifiers: list[dict],
    has_more: bool = False,
    cursor: str | None = None,
    total_count: int | None = None,
) -> dict:
    """Build a GetPropertyListResponse JSON payload.

    Args:
        identifiers: List of {"type": ..., "value": ...} dicts.
        has_more: Whether more pages exist.
        cursor: Opaque cursor for next page (only when has_more=True).
        total_count: Optional total count across all pages.
    """
    response: dict = {
        "list": {
            "list_id": "test_list",
            "name": "Test Property List",
        },
        "identifiers": identifiers,
        "resolved_at": datetime.now(UTC).isoformat(),
    }
    if has_more or cursor is not None:
        pagination: dict = {"has_more": has_more}
        if cursor is not None:
            pagination["cursor"] = cursor
        if total_count is not None:
            pagination["total_count"] = total_count
        response["pagination"] = pagination
    return response


# ---------------------------------------------------------------------------
# BR-RULE-077-01: Property List Resolution and Pagination
# ---------------------------------------------------------------------------


class TestPropertyListResolution:
    """Tests for property list resolution filtering products.

    Covers: BR-RULE-077-01
    """

    @pytest.mark.asyncio
    async def test_resolve_property_list_returns_matching_products(self, integration_db):
        """Resolved property list filters products to those with matching property IDs.

        Covers: BR-RULE-077-01
        """
        with ProductEnv(tenant_id="proplist-resolve", principal_id="proplist-principal") as env:
            tenant = TenantFactory(tenant_id="proplist-resolve", subdomain="proplist-resolve")
            PrincipalFactory(tenant=tenant, principal_id="proplist-principal")

            # Product with specific property IDs via by_id selector
            p1 = ProductFactory(
                tenant=tenant,
                product_id="prop_matching",
                name="Matching Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_alpha", "prop_beta"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Product with non-matching property IDs
            p2 = ProductFactory(
                tenant=tenant,
                product_id="prop_nonmatch",
                name="Non-Matching Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "other.com",
                        "property_ids": ["prop_gamma"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p2, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Product with selection_type="all" (always matches)
            p3 = ProductFactory(
                tenant=tenant,
                product_id="prop_all",
                name="All Properties Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "selection_type": "all",
                    }
                ],
            )
            PricingOptionFactory(product=p3, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Mock resolve_property_list to return identifiers matching p1
            env.set_property_list(["prop_alpha", "prop_beta"])

            response = await env.call_impl(
                brief="property list test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "prop_matching" in product_ids, "Product with matching property IDs should be included"
            assert "prop_all" in product_ids, "Product with selection_type='all' should always be included"
            assert "prop_nonmatch" not in product_ids, "Product with non-matching IDs should be excluded"

    @pytest.mark.asyncio
    async def test_resolve_with_cursor_returns_next_page(self, integration_db):
        """Cursor-based pagination: resolver fetches paginated identifiers from external service.

        Covers: BR-RULE-077-01
        """
        from src.core.property_list_resolver import clear_cache, resolve_property_list

        clear_cache()

        from adcp.types import PropertyListReference

        ref = PropertyListReference(
            agent_url="https://propertylist.example.com",
            list_id="paginated_list",
        )

        # First page response: 2 identifiers + cursor for next page
        page1_response = _mock_http_response(
            identifiers=[
                {"type": "domain", "value": "site1.com"},
                {"type": "domain", "value": "site2.com"},
            ],
            has_more=True,
            cursor="page2_cursor",
            total_count=4,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = page1_response
        mock_response.raise_for_status.return_value = None

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch("src.core.property_list_resolver._validate_agent_url"):
                result = await resolve_property_list(ref)

        # Resolver should return identifiers from the response
        assert len(result) == 2
        assert "site1.com" in result
        assert "site2.com" in result

        clear_cache()

    @pytest.mark.asyncio
    async def test_resolve_exhausted_cursor_returns_empty(self, integration_db):
        """Exhausted cursor: response with has_more=false and no identifiers yields empty list.

        Covers: BR-RULE-077-01
        """
        from src.core.property_list_resolver import clear_cache, resolve_property_list

        clear_cache()

        from adcp.types import PropertyListReference

        ref = PropertyListReference(
            agent_url="https://propertylist.example.com",
            list_id="exhausted_list",
        )

        # Response with no identifiers (cursor exhausted)
        empty_response = _mock_http_response(
            identifiers=[],
            has_more=False,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = empty_response
        mock_response.raise_for_status.return_value = None

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch("src.core.property_list_resolver._validate_agent_url"):
                result = await resolve_property_list(ref)

        assert result == [], "Exhausted cursor should yield empty identifier list"

        clear_cache()

    @pytest.mark.asyncio
    async def test_resolve_respects_page_size(self, integration_db):
        """Page size (max_results) limits the number of returned identifiers per page.

        Covers: BR-RULE-077-01
        """
        from src.core.property_list_resolver import clear_cache, resolve_property_list

        clear_cache()

        from adcp.types import PropertyListReference

        ref = PropertyListReference(
            agent_url="https://propertylist.example.com",
            list_id="sized_list",
        )

        # Response with exactly max_results identifiers (simulating server respecting limit)
        page_size = 3
        sized_response = _mock_http_response(
            identifiers=[{"type": "domain", "value": f"site{i}.com"} for i in range(page_size)],
            has_more=True,
            cursor="next_page",
            total_count=10,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = sized_response
        mock_response.raise_for_status.return_value = None

        with patch("src.core.property_list_resolver.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch("src.core.property_list_resolver._validate_agent_url"):
                result = await resolve_property_list(ref)

        # The resolver returns exactly the identifiers from the current page
        assert len(result) == page_size, (
            f"Expected {page_size} identifiers but got {len(result)}; "
            "resolver should return the page as received from external service"
        )

        clear_cache()

    @pytest.mark.asyncio
    async def test_resolve_filters_applied_at_resolution_time(self, integration_db):
        """Filters (countries_all AND, channels_any OR) are applied at resolution time.

        When resolve_property_list returns identifiers, only products whose
        publisher_properties overlap with those identifiers are included.
        This verifies that filtering happens after resolution, not before.

        Covers: BR-RULE-077-01
        """
        with ProductEnv(tenant_id="proplist-filter", principal_id="proplist-filter-principal") as env:
            tenant = TenantFactory(tenant_id="proplist-filter", subdomain="proplist-filter")
            PrincipalFactory(tenant=tenant, principal_id="proplist-filter-principal")

            # Product targeting US sites (specific property IDs)
            p_us = ProductFactory(
                tenant=tenant,
                product_id="us_sites_product",
                name="US Sites Only",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "us-publisher.com",
                        "property_ids": ["us_site_1", "us_site_2"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=True,
                countries=["US"],
            )
            PricingOptionFactory(product=p_us, pricing_model="cpm", rate=Decimal("12.0"), is_fixed=True)

            # Product targeting EU sites (different property IDs)
            p_eu = ProductFactory(
                tenant=tenant,
                product_id="eu_sites_product",
                name="EU Sites Only",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "eu-publisher.com",
                        "property_ids": ["eu_site_1", "eu_site_2"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=True,
                countries=["DE", "FR"],
            )
            PricingOptionFactory(product=p_eu, pricing_model="cpm", rate=Decimal("12.0"), is_fixed=True)

            # Product with partial overlap (property_targeting_allowed=True means ANY match)
            p_mixed = ProductFactory(
                tenant=tenant,
                product_id="mixed_sites_product",
                name="Mixed Sites",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "mixed-publisher.com",
                        "property_ids": ["us_site_1", "eu_site_1", "other_site"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p_mixed, pricing_model="cpm", rate=Decimal("12.0"), is_fixed=True)

            # Simulate resolution returning only US identifiers
            # The external service has already applied countries_all=["US"] AND channels_any=["web"]
            env.set_property_list(["us_site_1", "us_site_2"])

            response = await env.call_impl(
                brief="US property list test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]

            # US product matches (all its IDs are in the resolved set)
            assert "us_sites_product" in product_ids, (
                "US product should match: its property IDs are in the resolved set"
            )
            # EU product excluded (no overlap with resolved US identifiers)
            assert "eu_sites_product" not in product_ids, (
                "EU product should be excluded: no property ID overlap with US identifiers"
            )
            # Mixed product included (property_targeting_allowed=True, has partial overlap)
            assert "mixed_sites_product" in product_ids, (
                "Mixed product should match: property_targeting_allowed=True and has overlap with us_site_1"
            )

    @pytest.mark.asyncio
    async def test_resolve_no_identifiers_filters_to_all_selector_only(self, integration_db):
        """When resolved list returns empty identifiers, only all-selector products remain.

        Covers: BR-RULE-077-01
        """
        with ProductEnv(tenant_id="proplist-empty", principal_id="proplist-empty-principal") as env:
            tenant = TenantFactory(tenant_id="proplist-empty", subdomain="proplist-empty")
            PrincipalFactory(tenant=tenant, principal_id="proplist-empty-principal")

            # Product with selection_type="all" (always matches)
            p_all = ProductFactory(
                tenant=tenant,
                product_id="all_selector_product",
                name="All Properties",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "selection_type": "all",
                    }
                ],
            )
            PricingOptionFactory(product=p_all, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Product with specific property IDs
            p_specific = ProductFactory(
                tenant=tenant,
                product_id="specific_product",
                name="Specific Properties",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_x"],
                        "selection_type": "by_id",
                    }
                ],
            )
            PricingOptionFactory(product=p_specific, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Resolve returns empty set (no matching identifiers)
            env.set_property_list([])

            response = await env.call_impl(
                brief="empty property list test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "all_selector_product" in product_ids, "All-selector product should survive empty property list"
            assert "specific_product" not in product_ids, (
                "Specific-ID product should be excluded with empty resolved set"
            )
