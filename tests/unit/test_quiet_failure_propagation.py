"""Enrichment service fail-open with exception narrowing.

Product decision (GitHub #1093): Optional enrichment services degrade
gracefully on expected service failures but propagate programming errors
(TypeError, AttributeError) immediately.

    Covers: BR-RULE-079-01

beads: salesagent-o3x6
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity


def _make_identity(tenant_id="test-tenant"):
    return ResolvedIdentity(
        principal_id="user-1",
        tenant_id=tenant_id,
        tenant={
            "tenant_id": tenant_id,
            "name": "Test",
            "subdomain": "test",
            "ad_server": {"adapter": "mock"},
            "advertising_policy": None,
        },
        protocol="mcp",
    )


def _make_request(brief="test brief"):
    from src.core.schema_helpers import create_get_products_request

    return create_get_products_request(brief=brief)


def _mock_uow_with_products(products):
    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.products.list_all.return_value = products
    return mock_uow


def _base_patches(mock_uow, convert_fn=None):
    """Patches for a minimal _get_products_impl call (no enrichment)."""
    if convert_fn is None:
        convert_fn = lambda p, **kw: p  # noqa: E731
    return [
        patch("src.core.database.repositories.uow.ProductUoW", return_value=mock_uow),
        patch("src.core.tools.products.get_principal_object", return_value=None),
        patch("src.core.tools.products.convert_product_model_to_schema", side_effect=convert_fn),
    ]


class TestDynamicVariantsExceptionPropagation:
    """Dynamic variant generation fail-open.

    Covers: UC-001-MAIN-41
    """

    @pytest.mark.asyncio
    async def test_type_error_propagates(self):
        """TypeError (bug) propagates, not swallowed.

        Covers: UC-001-MAIN-41
        """
        from tests.helpers.adcp_factories import create_test_product

        product = create_test_product(product_id="p1")
        mock_uow = _mock_uow_with_products([product])

        patches = _base_patches(mock_uow) + [
            patch(
                "src.services.dynamic_products.generate_variants_for_brief",
                new_callable=AsyncMock,
                side_effect=TypeError("NoneType has no attribute 'product_id'"),
            ),
            patch(
                "src.services.dynamic_pricing_service.DynamicPricingService",
                **{
                    "return_value.enrich_products_with_pricing.side_effect": lambda products, **kw: products,
                },
            ),
        ]

        import contextlib

        from src.core.tools.products import _get_products_impl

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with pytest.raises(TypeError, match="NoneType"):
                await _get_products_impl(_make_request(), _make_identity())

    @pytest.mark.asyncio
    async def test_runtime_error_is_graceful(self):
        """RuntimeError (service failure) degrades gracefully.

        Covers: UC-001-MAIN-41
        """
        from tests.helpers.adcp_factories import create_test_product

        product = create_test_product(product_id="p1")
        mock_uow = _mock_uow_with_products([product])

        patches = _base_patches(mock_uow) + [
            patch(
                "src.services.dynamic_products.generate_variants_for_brief",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Connection refused"),
            ),
            patch(
                "src.services.dynamic_pricing_service.DynamicPricingService",
                **{
                    "return_value.enrich_products_with_pricing.side_effect": lambda products, **kw: products,
                },
            ),
        ]

        import contextlib

        from src.core.tools.products import _get_products_impl

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = await _get_products_impl(_make_request(), _make_identity())
            # Should still return the static product despite variant failure
            assert len(result.products) == 1


class TestDynamicPricingExceptionPropagation:
    """Dynamic pricing fail-open.

    Covers: UC-001-MAIN-42
    """

    @pytest.mark.asyncio
    async def test_type_error_propagates(self):
        """TypeError (bug) propagates, not swallowed.

        Covers: UC-001-MAIN-42
        """
        from tests.helpers.adcp_factories import create_test_product

        product = create_test_product(product_id="p1")
        mock_uow = _mock_uow_with_products([product])

        mock_pricing_cls = MagicMock()
        mock_pricing_cls.return_value.enrich_products_with_pricing.side_effect = TypeError(
            "'NoneType' object is not subscriptable"
        )

        patches = [
            patch("src.core.database.repositories.uow.ProductUoW", return_value=mock_uow),
            patch("src.core.tools.products.get_principal_object", return_value=None),
            patch("src.core.tools.products.convert_product_model_to_schema", side_effect=lambda p, **kw: p),
            patch(
                "src.services.dynamic_products.generate_variants_for_brief",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("src.services.dynamic_pricing_service.DynamicPricingService", mock_pricing_cls),
        ]

        import contextlib

        from src.core.tools.products import _get_products_impl

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with pytest.raises(TypeError, match="not subscriptable"):
                await _get_products_impl(_make_request(), _make_identity())


class TestAIRankingExceptionPropagation:
    """AI ranking fail-open (obligation already existed).

    Covers: UC-001-MAIN-32
    """

    @pytest.mark.asyncio
    async def test_type_error_propagates(self):
        """TypeError (bug) propagates even though service failures degrade.

        Covers: UC-001-MAIN-32
        """
        from tests.helpers.adcp_factories import create_test_product

        product = create_test_product(product_id="p1")
        mock_uow = _mock_uow_with_products([product])

        # Need tenant with product_ranking_prompt to trigger AI ranking path
        identity = ResolvedIdentity(
            principal_id="user-1",
            tenant_id="test-tenant",
            tenant={
                "tenant_id": "test-tenant",
                "name": "Test",
                "subdomain": "test",
                "ad_server": {"adapter": "mock"},
                "advertising_policy": None,
                "product_ranking_prompt": "Rank by relevance",
            },
            protocol="mcp",
        )

        mock_factory = MagicMock()
        mock_factory.is_ai_enabled.return_value = True
        mock_factory.create_model.return_value = MagicMock()

        patches = _base_patches(mock_uow) + [
            patch(
                "src.services.dynamic_products.generate_variants_for_brief",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("src.services.dynamic_pricing_service.DynamicPricingService"),
            patch("src.services.ai.factory.get_factory", return_value=mock_factory),
            patch(
                "src.services.ai.agents.ranking_agent.create_ranking_agent",
                return_value=MagicMock(),
            ),
            patch(
                "src.services.ai.agents.ranking_agent.rank_products_async",
                new_callable=AsyncMock,
                side_effect=TypeError("unexpected keyword argument 'products'"),
            ),
        ]

        import contextlib

        from src.core.tools.products import _get_products_impl

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with pytest.raises(TypeError, match="unexpected keyword"):
                await _get_products_impl(_make_request(brief="video ads"), identity)


class TestAdapterAnnotationExceptionPropagation:
    """Adapter annotation fail-open.

    Covers: UC-001-MAIN-43
    """

    @pytest.mark.asyncio
    async def test_type_error_propagates(self):
        """TypeError (bug) propagates, not swallowed.

        Covers: UC-001-MAIN-43
        """
        from tests.helpers.adcp_factories import create_test_product

        product = create_test_product(product_id="p1")
        mock_uow = _mock_uow_with_products([product])

        # Need a principal to trigger adapter annotation path
        mock_principal = MagicMock()
        mock_principal.principal_id = "user-1"

        patches = [
            patch("src.core.database.repositories.uow.ProductUoW", return_value=mock_uow),
            patch("src.core.tools.products.get_principal_object", return_value=mock_principal),
            patch("src.core.tools.products.convert_product_model_to_schema", side_effect=lambda p, **kw: p),
            patch(
                "src.services.dynamic_products.generate_variants_for_brief",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("src.services.dynamic_pricing_service.DynamicPricingService"),
            patch(
                "src.core.helpers.adapter_helpers.get_adapter",
                side_effect=TypeError("'NoneType' object has no attribute 'get'"),
            ),
        ]

        import contextlib

        from src.core.tools.products import _get_products_impl

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with pytest.raises(TypeError, match="NoneType"):
                await _get_products_impl(_make_request(), _make_identity())
