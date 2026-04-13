"""Integration tests for property list filtering in get_products.

Tests the filtering logic that restricts products based on buyer property lists.
The property_list parameter on get_products allows buyers to specify which
publisher properties they want to target. Products are filtered based on
overlap between the buyer's allowed properties and the product's
publisher_properties.

Filtering rules:
- Products with selection_type="all" always match (they cover all properties)
- Products with no property overlap are EXCLUDED
- Products with property_targeting_allowed=false require ALL product properties
  to be in the allowed set (full subset match)
- Products with property_targeting_allowed=true require ANY intersection

Pure function unit tests are retained for extract/should_include/filter functions.
Integration tests verify the same logic end-to-end through _get_products_impl
with real database products.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from adcp.types.generated_poc.core.property_id import PropertyId
from adcp.types.generated_poc.core.publisher_property_selector import (
    PublisherPropertySelector,
    PublisherPropertySelector1,
    PublisherPropertySelector2,
)

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Helpers (for pure function unit tests)
# ---------------------------------------------------------------------------


def _make_selector_all(domain: str = "example.com") -> PublisherPropertySelector:
    """Create a 'select all' publisher property selector."""
    return PublisherPropertySelector(
        root=PublisherPropertySelector1(
            publisher_domain=domain,
            selection_type="all",
        )
    )


def _make_selector_by_id(property_ids: list[str], domain: str = "example.com") -> PublisherPropertySelector:
    """Create a 'by_id' publisher property selector."""
    return PublisherPropertySelector(
        root=PublisherPropertySelector2(
            publisher_domain=domain,
            selection_type="by_id",
            property_ids=[PropertyId(root=pid) for pid in property_ids],
        )
    )


def _make_property_list_ref(
    agent_url: str = "https://propertylist.example.com",
    list_id: str = "test_list",
) -> dict:
    """Build a PropertyListReference dict suitable for call_impl."""
    return {"agent_url": agent_url, "list_id": list_id}


# ---------------------------------------------------------------------------
# Pure function unit tests — test extract/should_include/filter directly
# ---------------------------------------------------------------------------


class TestExtractPropertyIds:
    """Test extraction of property IDs from publisher_properties.

    Pure function tests — these call src.core.tools.products directly.
    """

    def test_extract_from_by_id_selector(self):
        from src.core.tools.products import extract_product_property_ids

        selectors = [_make_selector_by_id(["prop_1", "prop_2"])]
        result = extract_product_property_ids(selectors)
        assert result == {"prop_1", "prop_2"}

    def test_extract_from_multiple_by_id_selectors(self):
        from src.core.tools.products import extract_product_property_ids

        selectors = [
            _make_selector_by_id(["prop_1", "prop_2"], domain="sitea.com"),
            _make_selector_by_id(["prop_3"], domain="siteb.com"),
        ]
        result = extract_product_property_ids(selectors)
        assert result == {"prop_1", "prop_2", "prop_3"}

    def test_all_selector_returns_none(self):
        """selection_type='all' means the product covers ALL properties, return None to signal this."""
        from src.core.tools.products import extract_product_property_ids

        selectors = [_make_selector_all()]
        result = extract_product_property_ids(selectors)
        assert result is None

    def test_mixed_all_and_by_id_returns_none(self):
        """If any selector is 'all', the product covers everything."""
        from src.core.tools.products import extract_product_property_ids

        selectors = [
            _make_selector_by_id(["prop_1"]),
            _make_selector_all(),
        ]
        result = extract_product_property_ids(selectors)
        assert result is None

    def test_empty_selectors_returns_empty_set(self):
        from src.core.tools.products import extract_product_property_ids

        result = extract_product_property_ids([])
        assert result == set()


# TestCreateGetProductsRequestWithPropertyList and TestCapabilitiesPropertyListFiltering
# are pure unit tests (no DB) — canonical versions live in tests/unit/test_product_property_list_filtering.py

# ---------------------------------------------------------------------------
# Integration tests — verify property list filtering through _get_products_impl
# with real database products via ProductEnv + factories
# ---------------------------------------------------------------------------


class TestPropertyListFilteringAllSelectorE2E:
    """Products with selection_type='all' always pass through filtering."""

    @pytest.mark.asyncio
    async def test_all_selector_always_included(self, integration_db):
        """Product with selection_type='all' is included regardless of resolved property list."""
        with ProductEnv(tenant_id="plf-all", principal_id="plf-all-p") as env:
            tenant = TenantFactory(tenant_id="plf-all", subdomain="plf-all")
            PrincipalFactory(tenant=tenant, principal_id="plf-all-p")

            p = ProductFactory(
                tenant=tenant,
                product_id="all_selector",
                name="All Properties Product",
                property_tags=None,
                properties=[{"publisher_domain": "example.com", "selection_type": "all"}],
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Resolve returns a narrow set — all-selector should still be included
            env.set_property_list(["prop_x", "prop_y"])

            response = await env.call_impl(
                brief="all selector test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "all_selector" in product_ids


class TestPropertyListFilteringNoOverlapE2E:
    """Products with no property overlap are excluded."""

    @pytest.mark.asyncio
    async def test_no_overlap_excluded(self, integration_db):
        """Product whose property IDs have zero overlap with resolved list is excluded."""
        with ProductEnv(tenant_id="plf-noovlp", principal_id="plf-noovlp-p") as env:
            tenant = TenantFactory(tenant_id="plf-noovlp", subdomain="plf-noovlp")
            PrincipalFactory(tenant=tenant, principal_id="plf-noovlp-p")

            p = ProductFactory(
                tenant=tenant,
                product_id="no_overlap",
                name="No Overlap Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a", "prop_b"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Resolved list has completely different property IDs
            env.set_property_list(["prop_x", "prop_y"])

            response = await env.call_impl(
                brief="no overlap test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "no_overlap" not in product_ids


class TestPropertyListFilteringTargetingAllowedE2E:
    """property_targeting_allowed=true: ANY intersection is sufficient."""

    @pytest.mark.asyncio
    async def test_partial_overlap_included_when_targeting_allowed(self, integration_db):
        """Product with property_targeting_allowed=true is included with partial overlap."""
        with ProductEnv(tenant_id="plf-partial", principal_id="plf-partial-p") as env:
            tenant = TenantFactory(tenant_id="plf-partial", subdomain="plf-partial")
            PrincipalFactory(tenant=tenant, principal_id="plf-partial-p")

            p = ProductFactory(
                tenant=tenant,
                product_id="partial_allowed",
                name="Partial Overlap Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a", "prop_b", "prop_c"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Resolved list has only one matching property ID
            env.set_property_list(["prop_a"])

            response = await env.call_impl(
                brief="partial overlap test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "partial_allowed" in product_ids


class TestPropertyListFilteringTargetingNotAllowedE2E:
    """property_targeting_allowed=false: ALL product properties must be in allowed set."""

    @pytest.mark.asyncio
    async def test_partial_overlap_excluded_when_targeting_not_allowed(self, integration_db):
        """Product with property_targeting_allowed=false is excluded with partial overlap."""
        with ProductEnv(tenant_id="plf-strict", principal_id="plf-strict-p") as env:
            tenant = TenantFactory(tenant_id="plf-strict", subdomain="plf-strict")
            PrincipalFactory(tenant=tenant, principal_id="plf-strict-p")

            p = ProductFactory(
                tenant=tenant,
                product_id="strict_partial",
                name="Strict Partial Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a", "prop_b"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Resolved list has prop_a but not prop_b — partial overlap
            env.set_property_list(["prop_a", "prop_c"])

            response = await env.call_impl(
                brief="strict partial test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "strict_partial" not in product_ids

    @pytest.mark.asyncio
    async def test_full_subset_included_when_targeting_not_allowed(self, integration_db):
        """Product with property_targeting_allowed=false is included when all its props are in allowed set."""
        with ProductEnv(tenant_id="plf-subset", principal_id="plf-subset-p") as env:
            tenant = TenantFactory(tenant_id="plf-subset", subdomain="plf-subset")
            PrincipalFactory(tenant=tenant, principal_id="plf-subset-p")

            p = ProductFactory(
                tenant=tenant,
                product_id="strict_subset",
                name="Strict Full Subset Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a", "prop_b"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Resolved list contains all product IDs plus extras
            env.set_property_list(["prop_a", "prop_b", "prop_c"])

            response = await env.call_impl(
                brief="strict subset test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "strict_subset" in product_ids

    @pytest.mark.asyncio
    async def test_exact_match_included_when_targeting_not_allowed(self, integration_db):
        """Product with property_targeting_allowed=false is included with exact match."""
        with ProductEnv(tenant_id="plf-exact", principal_id="plf-exact-p") as env:
            tenant = TenantFactory(tenant_id="plf-exact", subdomain="plf-exact")
            PrincipalFactory(tenant=tenant, principal_id="plf-exact-p")

            p = ProductFactory(
                tenant=tenant,
                product_id="strict_exact",
                name="Strict Exact Match Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Resolved list exactly matches product IDs
            env.set_property_list(["prop_a"])

            response = await env.call_impl(
                brief="strict exact test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "strict_exact" in product_ids


class TestPropertyListFilteringEmptyResolvedListE2E:
    """Empty resolved property list excludes all by_id products."""

    @pytest.mark.asyncio
    async def test_empty_resolved_excludes_by_id_keeps_all(self, integration_db):
        """Empty resolved set excludes by_id products but keeps selection_type='all'."""
        with ProductEnv(tenant_id="plf-empty", principal_id="plf-empty-p") as env:
            tenant = TenantFactory(tenant_id="plf-empty", subdomain="plf-empty")
            PrincipalFactory(tenant=tenant, principal_id="plf-empty-p")

            p_all = ProductFactory(
                tenant=tenant,
                product_id="all_selector_empty",
                name="All Selector Product",
                property_tags=None,
                properties=[{"publisher_domain": "example.com", "selection_type": "all"}],
            )
            PricingOptionFactory(product=p_all, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            p_specific = ProductFactory(
                tenant=tenant,
                product_id="specific_empty",
                name="Specific ID Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a"],
                        "selection_type": "by_id",
                    }
                ],
            )
            PricingOptionFactory(product=p_specific, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Empty resolved list
            env.set_property_list([])

            response = await env.call_impl(
                brief="empty resolved test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]
            assert "all_selector_empty" in product_ids
            assert "specific_empty" not in product_ids


class TestPropertyListFilteringCombinedE2E:
    """Combined filtering: multiple products with different property configs."""

    @pytest.mark.asyncio
    async def test_combined_filtering_correctness(self, integration_db):
        """End-to-end test with 5 products covering all filtering scenarios."""
        with ProductEnv(tenant_id="plf-combo", principal_id="plf-combo-p") as env:
            tenant = TenantFactory(tenant_id="plf-combo", subdomain="plf-combo")
            PrincipalFactory(tenant=tenant, principal_id="plf-combo-p")

            # 1. selection_type="all" — always matches
            p1 = ProductFactory(
                tenant=tenant,
                product_id="combo_all",
                name="All Properties",
                property_tags=None,
                properties=[{"publisher_domain": "example.com", "selection_type": "all"}],
            )
            PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # 2. Exact match with targeting_not_allowed — should match
            p2 = ProductFactory(
                tenant=tenant,
                product_id="combo_match",
                name="Matching Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p2, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # 3. No overlap — should be excluded
            p3 = ProductFactory(
                tenant=tenant,
                product_id="combo_no_match",
                name="No Match Product",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "other.com",
                        "property_ids": ["prop_x"],
                        "selection_type": "by_id",
                    }
                ],
            )
            PricingOptionFactory(product=p3, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # 4. Partial overlap + targeting_allowed=True — should match
            p4 = ProductFactory(
                tenant=tenant,
                product_id="combo_partial_ok",
                name="Partial With Targeting",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a", "prop_b"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=True,
            )
            PricingOptionFactory(product=p4, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # 5. Partial overlap + targeting_allowed=False — should be excluded
            p5 = ProductFactory(
                tenant=tenant,
                product_id="combo_partial_no",
                name="Partial Without Targeting",
                property_tags=None,
                properties=[
                    {
                        "publisher_domain": "example.com",
                        "property_ids": ["prop_a", "prop_b"],
                        "selection_type": "by_id",
                    }
                ],
                property_targeting_allowed=False,
            )
            PricingOptionFactory(product=p5, pricing_model="cpm", rate=Decimal("5.00"), is_fixed=True)

            # Resolved property list: only prop_a
            env.set_property_list(["prop_a"])

            response = await env.call_impl(
                brief="combined filtering test",
                property_list=_make_property_list_ref(),
            )

            product_ids = [p.product_id for p in response.products]

            assert "combo_all" in product_ids, "All-selector always matches"
            assert "combo_match" in product_ids, "Exact match with targeting_not_allowed"
            assert "combo_partial_ok" in product_ids, "Partial overlap with targeting_allowed"
            assert "combo_no_match" not in product_ids, "No overlap excluded"
            assert "combo_partial_no" not in product_ids, "Partial overlap with targeting_not_allowed excluded"
