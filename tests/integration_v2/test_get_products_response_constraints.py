"""Integration tests for get_products response schema & constraint obligations.

Tests behavioral constraints that require real database interaction:
- CONSTR-PRODUCT-UNIQUENESS-01: No duplicate product_ids in response
- CONSTR-RELEVANCE-THRESHOLD-01: AI ranking threshold filter behavior
- CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01: publisher_domains sorted in response

Every test method has a ``Covers: <obligation-id>`` tag in its docstring.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.factories import (
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    PublisherPartnerFactory,
    TenantFactory,
)
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Product ID Uniqueness (CONSTR-PRODUCT-UNIQUENESS-01)
# ---------------------------------------------------------------------------


class TestProductUniquenessIntegration:
    """Product ID uniqueness across the discovery pipeline."""

    @pytest.mark.asyncio
    async def test_no_duplicate_product_ids_in_response(self, integration_db):
        """get_products response contains no duplicate product_ids.

        Covers: CONSTR-PRODUCT-UNIQUENESS-01
        """
        with ProductEnv(tenant_id="uniq-t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="uniq-t1", subdomain="uniq-t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            # Create 3 distinct products
            for pid in ("prod_alpha", "prod_beta", "prod_gamma"):
                p = ProductFactory(tenant=tenant, product_id=pid)
                PricingOptionFactory(product=p)

            response = await env.call_impl(brief="display ads")
            product_ids = [p.product_id for p in response.products]
            assert len(product_ids) == len(set(product_ids)), f"Duplicate product_ids found in response: {product_ids}"

    @pytest.mark.asyncio
    async def test_multiple_products_have_distinct_ids(self, integration_db):
        """Multiple products in same tenant have distinct product_ids in response.

        Covers: CONSTR-PRODUCT-UNIQUENESS-01
        """
        with ProductEnv(tenant_id="uniq-t2", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="uniq-t2", subdomain="uniq-t2")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            # Create products with distinct IDs
            for pid in ("unique_1", "unique_2", "unique_3"):
                p = ProductFactory(tenant=tenant, product_id=pid)
                PricingOptionFactory(product=p)

            response = await env.call_impl(brief="unique test")
            ids = [p.product_id for p in response.products]
            # Database guarantees product_id uniqueness per tenant
            assert len(ids) == len(set(ids))
            assert set(ids) == {"unique_1", "unique_2", "unique_3"}


# ---------------------------------------------------------------------------
# Relevance Threshold (CONSTR-RELEVANCE-THRESHOLD-01)
# ---------------------------------------------------------------------------


class TestRelevanceThresholdIntegration:
    """AI ranking threshold behavior in the pipeline."""

    @pytest.mark.asyncio
    async def test_score_below_threshold_excluded(self, integration_db):
        """Products with relevance score < 0.1 are excluded when ranking is active.

        Covers: CONSTR-RELEVANCE-THRESHOLD-01
        """
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tenant_context import LazyTenantContext
        from src.core.testing_hooks import AdCPTestContext
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        with ProductEnv(tenant_id="thresh-t1", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="thresh-t1",
                subdomain="thresh-t1",
                product_ranking_prompt="Rank by relevance",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")

            for pid in ("above", "at_boundary", "below"):
                p = ProductFactory(tenant=tenant, product_id=pid)
                PricingOptionFactory(product=p)

            ranking_result = ProductRankingResult(
                rankings=[
                    ProductRanking(product_id="above", relevance_score=0.5, reason="Relevant"),
                    ProductRanking(product_id="at_boundary", relevance_score=0.1, reason="Boundary"),
                    ProductRanking(product_id="below", relevance_score=0.09, reason="Not relevant"),
                ]
            )

            env._identity = ResolvedIdentity(
                principal_id="p1",
                tenant_id="thresh-t1",
                tenant=LazyTenantContext("thresh-t1"),
                protocol="mcp",
                testing_context=AdCPTestContext(
                    dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None
                ),
            )

            with (
                patch(
                    "src.services.ai.agents.ranking_agent.rank_products_async",
                    new_callable=AsyncMock,
                ) as mock_rank,
                patch("src.services.ai.agents.ranking_agent.create_ranking_agent"),
                patch("src.services.ai.factory.get_factory") as mock_factory,
            ):
                factory_inst = MagicMock()
                factory_inst.is_ai_enabled.return_value = True
                factory_inst.create_model.return_value = MagicMock()
                mock_factory.return_value = factory_inst
                mock_rank.return_value = ranking_result

                # Stop the harness's default ranking mock so our patch takes effect
                env.mock["ranking_factory"].stop()

                response = await env.call_impl(brief="sports content")

            returned_ids = {p.product_id for p in response.products}
            assert "above" in returned_ids, "Product with score 0.5 should be included"
            assert "at_boundary" in returned_ids, "Product with score 0.1 should be included (boundary)"
            assert "below" not in returned_ids, "Product with score 0.09 should be excluded"

    @pytest.mark.asyncio
    async def test_no_ranking_returns_all_products(self, integration_db):
        """Without ranking active, all products are returned (no threshold).

        Covers: CONSTR-RELEVANCE-THRESHOLD-01
        """
        with ProductEnv(tenant_id="thresh-t2", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="thresh-t2", subdomain="thresh-t2")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            for pid in ("all_a", "all_b", "all_c"):
                p = ProductFactory(tenant=tenant, product_id=pid)
                PricingOptionFactory(product=p)

            # ranking is disabled by default in ProductEnv
            response = await env.call_impl(brief="all products")
            returned_ids = {p.product_id for p in response.products}
            assert returned_ids == {"all_a", "all_b", "all_c"}


# ---------------------------------------------------------------------------
# Publisher Domains Portfolio (CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01)
# ---------------------------------------------------------------------------


class TestPublisherDomainsPortfolioIntegration:
    """Publisher domains portfolio assembly from real database."""

    @pytest.mark.asyncio
    async def test_publisher_domains_from_list_authorized_properties(self, integration_db):
        """list_authorized_properties returns publisher_domains sorted alphabetically.

        Covers: CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01
        """
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tenant_context import LazyTenantContext
        from src.core.tools.properties import _list_authorized_properties_impl

        with ProductEnv(tenant_id="pub-dom-t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="pub-dom-t1", subdomain="pub-dom-t1")

            # Add publishers in non-alphabetical order
            for domain in ["zeta.com", "alpha.com", "mike.com"]:
                PublisherPartnerFactory(tenant=tenant, publisher_domain=domain)

            identity = ResolvedIdentity(
                principal_id=None,
                tenant_id="pub-dom-t1",
                tenant=LazyTenantContext("pub-dom-t1"),
                protocol="mcp",
            )

            response = _list_authorized_properties_impl(req=None, identity=identity)
            assert response.publisher_domains == ["alpha.com", "mike.com", "zeta.com"]

    @pytest.mark.asyncio
    async def test_empty_publisher_domains_returns_empty_array(self, integration_db):
        """Tenant with no publishers returns empty array, not null.

        Covers: CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01
        """
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tenant_context import LazyTenantContext
        from src.core.tools.properties import _list_authorized_properties_impl

        with ProductEnv(tenant_id="pub-dom-t2", principal_id="p1") as env:
            TenantFactory(tenant_id="pub-dom-t2", subdomain="pub-dom-t2")

            identity = ResolvedIdentity(
                principal_id=None,
                tenant_id="pub-dom-t2",
                tenant=LazyTenantContext("pub-dom-t2"),
                protocol="mcp",
            )

            response = _list_authorized_properties_impl(req=None, identity=identity)
            assert response.publisher_domains == []
            assert isinstance(response.publisher_domains, list)
