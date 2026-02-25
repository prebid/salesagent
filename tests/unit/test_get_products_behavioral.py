"""Behavioral snapshot tests for get_products (UC-001).

These tests pin down _get_products_impl behavior before FastAPI migration.
Each test is traced to a BDD scenario from BR-UC-001-discover-available-inventory.feature.

Tests are ordered by migration risk: HIGH_RISK first, then MEDIUM_RISK.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from src.core.tools.products import _get_products_impl
from src.services.policy_check_service import PolicyCheckResult, PolicyStatus
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_publisher_properties_by_tag,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_request(
    brief: str = "Athletic footwear",
    brand_manifest=None,
    filters=None,
    context=None,
):
    """Create a mock GetProductsRequest.

    Explicitly sets getattr-accessed fields to None to prevent MagicMock
    auto-creating truthy values that interfere with pipeline logic.
    """
    mock_request = MagicMock()
    mock_request.brief = brief
    mock_request.brand_manifest = brand_manifest
    mock_request.filters = filters
    mock_request.context = context
    # _get_products_impl reads these via getattr() -- avoid truthy MagicMock
    mock_request.min_exposures = None
    return mock_request


def _make_mock_context():
    """Create a mock FastMCP Context."""
    return MagicMock()


def _make_tenant(
    tenant_id: str = "test_tenant",
    brand_manifest_policy: str = "public",
    advertising_policy: dict | None = None,
    product_ranking_prompt: str | None = None,
    **extra,
):
    """Create a mock tenant dict with common defaults."""
    tenant = {
        "tenant_id": tenant_id,
        "brand_manifest_policy": brand_manifest_policy,
        "advertising_policy": advertising_policy or {},
        **extra,
    }
    if product_ranking_prompt is not None:
        tenant["product_ranking_prompt"] = product_ranking_prompt
    return tenant


def _make_real_product(product_id: str = "prod_1", **kwargs):
    """Create a real Product object for pipeline tests.

    Uses our extended Product (src.core.schemas) which has
    allowed_principal_ids, so access control tests work correctly.
    The object passes Pydantic validation for GetProductsResponse.
    """
    from src.core.schemas import Product

    allowed_principal_ids = kwargs.pop("allowed_principal_ids", None)
    format_ids = kwargs.get("format_ids") or [
        {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}
    ]
    product = Product(
        product_id=product_id,
        name=kwargs.get("name", f"Product {product_id}"),
        description=kwargs.get("description", "Test product"),
        format_ids=format_ids,
        delivery_type=kwargs.get("delivery_type", "guaranteed"),
        pricing_options=kwargs.get(
            "pricing_options",
            [create_test_cpm_pricing_option(pricing_option_id=f"po_{product_id}")],
        ),
        publisher_properties=kwargs.get(
            "publisher_properties",
            [create_test_publisher_properties_by_tag()],
        ),
        delivery_measurement=kwargs.get(
            "delivery_measurement",
            {"provider": "test_provider", "notes": "Test measurement methodology"},
        ),
        allowed_principal_ids=allowed_principal_ids,
    )
    return product


def _mock_db_returning_products(products_to_return, mock_db_session):
    """Configure mock_db_session to return given products from DB query."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.unique.return_value.scalars.return_value.all.return_value = products_to_return
    mock_session.execute.return_value = mock_result
    mock_db_session.return_value.__enter__.return_value = mock_session
    return mock_session


def _setup_standard_mocks(
    patches,
    tenant,
    schema_products,
    principal_id="principal_1",
):
    """Wire up the standard mock chain for _get_products_impl.

    Args:
        patches: dict of active patch context managers (already entered)
        tenant: tenant dict
        schema_products: list of real Product objects returned by convert_product_model_to_schema
        principal_id: principal ID or None for anonymous

    Returns:
        None -- mutates the patches in place
    """
    patches["get_principal"].return_value = (principal_id, tenant)
    patches["get_principal_obj"].return_value = None
    patches["get_testing"].return_value = None
    patches["generate_variants"].return_value = []

    mock_pricing_inst = MagicMock()
    mock_pricing_inst.enrich_products_with_pricing.side_effect = lambda prods, **kw: prods
    patches["pricing_service"].return_value = mock_pricing_inst

    # DB returns placeholder models; convert_product transforms them to real Products
    db_models = [MagicMock() for _ in schema_products]
    _mock_db_returning_products(db_models, patches["db_session"])
    patches["convert_product"].side_effect = list(schema_products)

    patches["audit_logger"].return_value = MagicMock()


# Context manager that starts all standard patches
class _PipelinePatches:
    """Context manager that starts all standard patches and provides access."""

    TARGETS = {
        "get_principal": "src.core.tools.products.get_principal_from_context",
        "get_principal_obj": "src.core.tools.products.get_principal_object",
        "get_testing": "src.core.tools.products.get_testing_context",
        "set_tenant": "src.core.tools.products.set_current_tenant",
        "generate_variants": "src.services.dynamic_products.generate_variants_for_brief",
        "pricing_service": "src.services.dynamic_pricing_service.DynamicPricingService",
        "db_session": "src.core.tools.products.get_db_session",
        "convert_product": "src.core.tools.products.convert_product_model_to_schema",
        "audit_logger": "src.core.tools.products.get_audit_logger",
    }

    def __enter__(self):
        self._patchers = {}
        self._mocks = {}
        for name, target in self.TARGETS.items():
            p = patch(target)
            self._patchers[name] = p
            self._mocks[name] = p.start()
        return self._mocks

    def __exit__(self, *args):
        for p in self._patchers.values():
            p.stop()


# ---------------------------------------------------------------------------
# HIGH_RISK tests
# ---------------------------------------------------------------------------

# ---- Ranking: tests 1, 2 (S18, S19, S30, S37) ----


class TestRankingThresholdBehavior:
    """Tests for AI ranking threshold, sort order, and boundary values.

    BDD scenarios: T-UC-001-rule-005, T-UC-001-partition-relevance,
    T-UC-001-boundary-relevance (S18, S30, S37)
    """

    @pytest.mark.asyncio
    async def test_ranking_sorts_descending_and_filters_below_threshold(self):
        """When brief provided + AI scores products, threshold >= 0.1 applied, sorted descending."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        p_high = _make_real_product(product_id="p_high")
        p_med = _make_real_product(product_id="p_med")
        p_low = _make_real_product(product_id="p_low")
        p_excluded = _make_real_product(product_id="p_excluded")
        products = [p_high, p_med, p_low, p_excluded]

        tenant = _make_tenant(product_ranking_prompt="Rank by relevance to sports")

        ranking_result = ProductRankingResult(
            rankings=[
                ProductRanking(product_id="p_high", relevance_score=0.9, reason="Very relevant"),
                ProductRanking(product_id="p_med", relevance_score=0.5, reason="Somewhat relevant"),
                ProductRanking(product_id="p_low", relevance_score=0.1, reason="Barely relevant"),
                ProductRanking(product_id="p_excluded", relevance_score=0.09, reason="Not relevant"),
            ]
        )

        mock_request = _make_mock_request(brief="sports equipment campaign")

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, products)

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

                response = await _get_products_impl(mock_request, _make_mock_context())

        # 3 products above threshold, p_excluded (0.09) filtered out
        assert len(response.products) == 3
        assert response.products[0].product_id == "p_high"
        assert response.products[1].product_id == "p_med"
        assert response.products[2].product_id == "p_low"

    @pytest.mark.asyncio
    async def test_ranking_boundary_score_0_1_included(self):
        """Score exactly 0.1 should be INCLUDED (>= 0.1 threshold)."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        product = _make_real_product(product_id="p_boundary")
        tenant = _make_tenant(product_ranking_prompt="Rank products")

        ranking_result = ProductRankingResult(
            rankings=[ProductRanking(product_id="p_boundary", relevance_score=0.1, reason="Boundary")]
        )

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product])

            with (
                patch("src.services.ai.agents.ranking_agent.rank_products_async", new_callable=AsyncMock) as mock_rank,
                patch("src.services.ai.agents.ranking_agent.create_ranking_agent"),
                patch("src.services.ai.factory.get_factory") as mock_factory,
            ):
                factory_inst = MagicMock()
                factory_inst.is_ai_enabled.return_value = True
                factory_inst.create_model.return_value = MagicMock()
                mock_factory.return_value = factory_inst
                mock_rank.return_value = ranking_result

                response = await _get_products_impl(_make_mock_request(brief="campaign"), _make_mock_context())

        assert len(response.products) == 1
        assert response.products[0].product_id == "p_boundary"

    @pytest.mark.asyncio
    async def test_ranking_boundary_score_0_09_excluded(self):
        """Score 0.09 should be EXCLUDED (< 0.1 threshold)."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        product = _make_real_product(product_id="p_below")
        tenant = _make_tenant(product_ranking_prompt="Rank products")

        ranking_result = ProductRankingResult(
            rankings=[ProductRanking(product_id="p_below", relevance_score=0.09, reason="Below")]
        )

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product])

            with (
                patch("src.services.ai.agents.ranking_agent.rank_products_async", new_callable=AsyncMock) as mock_rank,
                patch("src.services.ai.agents.ranking_agent.create_ranking_agent"),
                patch("src.services.ai.factory.get_factory") as mock_factory,
            ):
                factory_inst = MagicMock()
                factory_inst.is_ai_enabled.return_value = True
                factory_inst.create_model.return_value = MagicMock()
                mock_factory.return_value = factory_inst
                mock_rank.return_value = ranking_result

                response = await _get_products_impl(_make_mock_request(brief="campaign"), _make_mock_context())

        assert len(response.products) == 0

    @pytest.mark.asyncio
    async def test_brief_relevance_not_set_on_products(self):
        """brief_relevance is NOT_IMPLEMENTED -- field should be absent/None after ranking."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        product = _make_real_product(product_id="p1")
        tenant = _make_tenant(product_ranking_prompt="Rank products")

        ranking_result = ProductRankingResult(
            rankings=[ProductRanking(product_id="p1", relevance_score=0.8, reason="Good")]
        )

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product])

            with (
                patch("src.services.ai.agents.ranking_agent.rank_products_async", new_callable=AsyncMock) as mock_rank,
                patch("src.services.ai.agents.ranking_agent.create_ranking_agent"),
                patch("src.services.ai.factory.get_factory") as mock_factory,
            ):
                factory_inst = MagicMock()
                factory_inst.is_ai_enabled.return_value = True
                factory_inst.create_model.return_value = MagicMock()
                mock_factory.return_value = factory_inst
                mock_rank.return_value = ranking_result

                response = await _get_products_impl(_make_mock_request(brief="campaign"), _make_mock_context())

        # brief_relevance is NOT_IMPLEMENTED -- _get_products_impl never assigns it
        for p in response.products:
            assert getattr(p, "brief_relevance", None) is None, (
                "brief_relevance should NOT be set by _get_products_impl (NOT_IMPLEMENTED)"
            )


class TestRankingFailureFailopen:
    """Test AI ranking failure results in fail-open behavior.

    BDD scenario: T-UC-001-rule-005-fail (S19)
    """

    @pytest.mark.asyncio
    async def test_ranking_failure_returns_products_unranked(self):
        """When AI ranking service raises Exception, products returned in catalog order."""
        p1 = _make_real_product(product_id="p_first")
        p2 = _make_real_product(product_id="p_second")
        tenant = _make_tenant(product_ranking_prompt="Rank products")

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [p1, p2])

            # Mock AI ranking to RAISE an exception
            with patch("src.services.ai.factory.get_factory") as mock_factory:
                factory_inst = MagicMock()
                factory_inst.is_ai_enabled.return_value = True
                factory_inst.create_model.side_effect = RuntimeError("AI service unavailable")
                mock_factory.return_value = factory_inst

                # Should NOT raise -- fail-open
                response = await _get_products_impl(_make_mock_request(brief="campaign"), _make_mock_context())

        # Products returned in catalog order (original DB order)
        assert len(response.products) == 2
        assert response.products[0].product_id == "p_first"
        assert response.products[1].product_id == "p_second"


# ---- Policy: tests 3, 4, 5 (S9, S8, S10) ----


class TestPolicyBlockedPipelineRejection:
    """Test BLOCKED policy raises ToolError through _get_products_impl pipeline.

    BDD scenario: T-UC-001-ext-a-blocked (S8)
    """

    @pytest.mark.asyncio
    async def test_blocked_policy_raises_tool_error(self):
        """When policy returns BLOCKED, ToolError('POLICY_VIOLATION') raised with reason."""
        tenant = _make_tenant(
            advertising_policy={"enabled": True},
            gemini_api_key="test-key",
        )

        policy_result = PolicyCheckResult(
            status=PolicyStatus.BLOCKED,
            reason="Prohibited content: gambling",
        )

        with (
            patch("src.core.tools.products.get_principal_from_context") as mock_gp,
            patch("src.core.tools.products.get_principal_object"),
            patch("src.core.tools.products.get_testing_context") as mock_gt,
            patch("src.core.tools.products.set_current_tenant"),
            patch("src.core.tools.products.PolicyCheckService") as mock_policy_cls,
            patch("src.core.tools.products.get_audit_logger") as mock_al,
        ):
            mock_gp.return_value = ("principal_1", tenant)
            mock_gt.return_value = None
            mock_al.return_value = MagicMock()

            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(return_value=policy_result)
            mock_policy_cls.return_value = mock_policy_inst

            with pytest.raises(ToolError) as exc_info:
                await _get_products_impl(_make_mock_request(brief="Online gambling"), _make_mock_context())

        assert "POLICY_VIOLATION" in str(exc_info.value)
        assert "gambling" in str(exc_info.value).lower()


class TestRestrictedBriefManualReviewRejection:
    """Test RESTRICTED + require_manual_review raises ToolError.

    BDD scenario: T-UC-001-ext-a-restricted (S9)
    """

    @pytest.mark.asyncio
    async def test_restricted_with_manual_review_raises_tool_error(self):
        """When RESTRICTED + require_manual_review=True, ToolError('POLICY_VIOLATION') raised."""
        tenant = _make_tenant(
            advertising_policy={"enabled": True, "require_manual_review": True},
            gemini_api_key="test-key",
        )

        policy_result = PolicyCheckResult(
            status=PolicyStatus.RESTRICTED,
            reason="Content may violate alcohol advertising guidelines",
            restrictions=["alcohol_marketing"],
        )

        with (
            patch("src.core.tools.products.get_principal_from_context") as mock_gp,
            patch("src.core.tools.products.get_principal_object"),
            patch("src.core.tools.products.get_testing_context") as mock_gt,
            patch("src.core.tools.products.set_current_tenant"),
            patch("src.core.tools.products.PolicyCheckService") as mock_policy_cls,
            patch("src.core.tools.products.get_db_session") as mock_db,
            patch("src.core.tools.products.get_audit_logger") as mock_al,
        ):
            mock_gp.return_value = ("principal_1", tenant)
            mock_gt.return_value = None
            mock_al.return_value = MagicMock()

            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(return_value=policy_result)
            mock_policy_cls.return_value = mock_policy_inst

            # DB session for audit log in restricted+manual_review branch
            mock_db.return_value.__enter__.return_value = MagicMock()

            with pytest.raises(ToolError) as exc_info:
                await _get_products_impl(_make_mock_request(brief="Craft beer festival"), _make_mock_context())

        assert "POLICY_VIOLATION" in str(exc_info.value)
        assert "alcohol" in str(exc_info.value).lower()


class TestPolicyServiceFailopenPipeline:
    """Test policy service exception results in fail-open behavior.

    BDD scenario: T-UC-001-ext-a-failopen (S10)
    """

    @pytest.mark.asyncio
    async def test_policy_exception_returns_products_normally(self):
        """When PolicyCheckService.check_brief_compliance raises, products still returned."""
        tenant = _make_tenant(
            advertising_policy={"enabled": True},
            gemini_api_key="test-key",
        )
        product = _make_real_product(product_id="p1")

        with (
            _PipelinePatches() as mocks,
            patch("src.core.tools.products.PolicyCheckService") as mock_policy_cls,
        ):
            _setup_standard_mocks(mocks, tenant, [product])

            # Policy service raises RuntimeError
            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(side_effect=RuntimeError("Gemini API timeout"))
            mock_policy_cls.return_value = mock_policy_inst

            response = await _get_products_impl(_make_mock_request(brief="Normal campaign"), _make_mock_context())

        # Fail-open: products returned despite policy exception
        assert len(response.products) == 1
        assert response.products[0].product_id == "p1"


# ---- Adapter annotation: test 6 (S25) ----


class TestAdapterSupportAnnotation:
    """Test pricing options annotated with adapter support info.

    BDD scenario: T-UC-001-adapter (S25)

    Note: _get_products_impl annotates the inner discriminated union objects
    via dynamic attribute assignment (type: ignore[union-attr]), so we use
    MagicMock pricing internals to observe the annotation.
    """

    @pytest.mark.asyncio
    async def test_supported_pricing_annotated(self):
        """When adapter supports pricing model, supported=True is set on inner."""
        # Create product with a real pricing option structure
        product = _make_real_product(product_id="p1")
        tenant = _make_tenant()
        mock_principal = MagicMock()

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product])
            mocks["get_principal_obj"].return_value = mock_principal

            with patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter:
                mock_adapter = MagicMock()
                mock_adapter.get_supported_pricing_models.return_value = {"cpm", "cpc"}
                mock_get_adapter.return_value = mock_adapter

                response = await _get_products_impl(_make_mock_request(brief="campaign"), _make_mock_context())

        # The CPM pricing option should be annotated as supported
        assert len(response.products) == 1
        assert len(response.products[0].pricing_options) == 1
        inner = response.products[0].pricing_options[0].root
        assert inner.supported is True

    @pytest.mark.asyncio
    async def test_unsupported_pricing_annotated_with_reason(self):
        """When adapter does NOT support pricing model, unsupported_reason is set."""
        # Create product with VCPM pricing
        product = _make_real_product(
            product_id="p1",
            pricing_options=[
                {
                    "pricing_option_id": "vcpm_1",
                    "pricing_model": "vcpm",
                    "currency": "USD",
                    "rate": 15.0,
                    "is_fixed": True,
                }
            ],
        )
        tenant = _make_tenant()
        mock_principal = MagicMock()

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product])
            mocks["get_principal_obj"].return_value = mock_principal

            with patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter:
                mock_adapter = MagicMock()
                # Only CPM supported -- VCPM not supported
                mock_adapter.get_supported_pricing_models.return_value = {"cpm"}
                mock_get_adapter.return_value = mock_adapter

                response = await _get_products_impl(_make_mock_request(brief="campaign"), _make_mock_context())

        inner = response.products[0].pricing_options[0].root
        assert inner.supported is False
        assert "VCPM" in inner.unsupported_reason


# ---- Empty results pipeline stages: test 7 (S5) ----


class TestEmptyResultsPipelineStages:
    """Test that each pipeline stage can produce empty results.

    BDD scenario: T-UC-001-alt-empty-causes (S5)
    """

    @pytest.mark.asyncio
    async def test_empty_catalog_returns_empty(self):
        """When no products exist in DB, empty products returned."""
        tenant = _make_tenant()

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, schema_products=[])

            response = await _get_products_impl(_make_mock_request(), _make_mock_context())

        assert response.products == []

    @pytest.mark.asyncio
    async def test_access_control_filters_all_returns_empty(self):
        """When all products are restricted and user is anonymous, empty returned."""
        tenant = _make_tenant()
        # Product restricted to a specific principal
        product = _make_real_product(product_id="restricted_p", allowed_principal_ids=["other_principal"])

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product], principal_id=None)

            response = await _get_products_impl(_make_mock_request(), _make_mock_context())

        assert response.products == []

    @pytest.mark.asyncio
    async def test_filter_mismatch_returns_empty(self):
        """When delivery_type filter matches nothing, empty returned."""
        tenant = _make_tenant()
        product = _make_real_product(product_id="p1", delivery_type="guaranteed")

        # Filters that don't match the product
        mock_filters = MagicMock()
        mock_filters.delivery_type = "non_guaranteed"
        mock_filters.is_fixed_price = None
        mock_filters.format_types = None
        mock_filters.format_ids = None
        mock_filters.standard_formats_only = None
        mock_filters.countries = None
        mock_filters.channels = None

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product])

            response = await _get_products_impl(_make_mock_request(filters=mock_filters), _make_mock_context())

        assert response.products == []

    @pytest.mark.asyncio
    async def test_ranking_threshold_eliminates_all_returns_empty(self):
        """When ranking scores are all below threshold, empty returned."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        product = _make_real_product(product_id="p1")
        tenant = _make_tenant(product_ranking_prompt="Rank products")

        ranking_result = ProductRankingResult(
            rankings=[ProductRanking(product_id="p1", relevance_score=0.05, reason="Not relevant")]
        )

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product])

            with (
                patch("src.services.ai.agents.ranking_agent.rank_products_async", new_callable=AsyncMock) as mock_rank,
                patch("src.services.ai.agents.ranking_agent.create_ranking_agent"),
                patch("src.services.ai.factory.get_factory") as mock_factory,
            ):
                factory_inst = MagicMock()
                factory_inst.is_ai_enabled.return_value = True
                factory_inst.create_model.return_value = MagicMock()
                mock_factory.return_value = factory_inst
                mock_rank.return_value = ranking_result

                response = await _get_products_impl(_make_mock_request(brief="unrelated brief"), _make_mock_context())

        assert response.products == []


# ---------------------------------------------------------------------------
# MEDIUM_RISK tests
# ---------------------------------------------------------------------------

# ---- Policy compliance matrix: test 8 (S14, S27, S34) ----


class TestBriefPolicyComplianceMatrix:
    """Parametrized test for the 7-row brief policy compliance matrix.

    BDD scenarios: T-UC-001-rule-002, T-UC-001-partition-brief-policy,
    T-UC-001-boundary-brief-policy (S14, S27, S34)
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "policy_enabled, has_api_key, policy_side_effect, policy_status, "
        "require_manual_review, expect_error, error_substring",
        [
            # Row 1: BLOCKED -> POLICY_VIOLATION
            (True, True, None, PolicyStatus.BLOCKED, False, True, "POLICY_VIOLATION"),
            # Row 2: RESTRICTED + manual_review -> POLICY_VIOLATION
            (True, True, None, PolicyStatus.RESTRICTED, True, True, "POLICY_VIOLATION"),
            # Row 3: RESTRICTED without manual_review -> success
            (True, True, None, PolicyStatus.RESTRICTED, False, False, None),
            # Row 4: APPROVED -> success
            (True, True, None, PolicyStatus.ALLOWED, False, False, None),
            # Row 5: Disabled -> success (policy check skipped)
            (False, True, None, None, False, False, None),
            # Row 6: No API key -> success (policy check skipped)
            (True, False, None, None, False, False, None),
            # Row 7: Service unavailable -> success (fail-open)
            (True, True, RuntimeError("Down"), None, False, False, None),
        ],
        ids=[
            "BLOCKED",
            "RESTRICTED+manual_review",
            "RESTRICTED_no_review",
            "APPROVED",
            "disabled",
            "no_api_key",
            "service_unavailable",
        ],
    )
    async def test_brief_policy_matrix_row(
        self,
        policy_enabled,
        has_api_key,
        policy_side_effect,
        policy_status,
        require_manual_review,
        expect_error,
        error_substring,
    ):
        """Verify each row of the brief policy compliance matrix."""
        adv_policy = {"enabled": policy_enabled}
        if require_manual_review:
            adv_policy["require_manual_review"] = True

        tenant = _make_tenant(
            advertising_policy=adv_policy,
            **({"gemini_api_key": "test-key"} if has_api_key else {}),
        )

        product = _make_real_product(product_id="p1")

        # Build policy result if applicable
        mock_policy_result = None
        if policy_status is not None:
            mock_policy_result = PolicyCheckResult(
                status=policy_status,
                reason="Test reason",
                restrictions=["test_restriction"],
            )

        with (
            _PipelinePatches() as mocks,
            patch("src.core.tools.products.PolicyCheckService") as mock_policy_cls,
        ):
            _setup_standard_mocks(mocks, tenant, [product])

            mock_policy_inst = MagicMock()
            if policy_side_effect:
                mock_policy_inst.check_brief_compliance = AsyncMock(side_effect=policy_side_effect)
            elif mock_policy_result:
                mock_policy_inst.check_brief_compliance = AsyncMock(return_value=mock_policy_result)
                mock_policy_inst.check_product_eligibility.return_value = (True, None)
            mock_policy_cls.return_value = mock_policy_inst

            if expect_error:
                with pytest.raises(ToolError) as exc_info:
                    await _get_products_impl(_make_mock_request(brief="test"), _make_mock_context())
                assert error_substring in str(exc_info.value)
            else:
                response = await _get_products_impl(_make_mock_request(brief="test"), _make_mock_context())
                assert response is not None


# ---- No brief skips ranking: test 10 (S2) ----


class TestNoBriefSkipsRanking:
    """Test that absent brief skips ranking and returns catalog order.

    BDD scenario: T-UC-001-alt-no-brief (S2)
    """

    @pytest.mark.asyncio
    async def test_no_brief_returns_catalog_order(self):
        """When brief is empty, ranking skipped, products returned in DB order."""
        p1 = _make_real_product(product_id="first_in_db")
        p2 = _make_real_product(product_id="second_in_db")
        tenant = _make_tenant(product_ranking_prompt="Rank products")

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [p1, p2])

            # AI ranking should NOT be called when brief is empty
            with patch("src.services.ai.factory.get_factory") as mock_factory:
                response = await _get_products_impl(_make_mock_request(brief=""), _make_mock_context())
                mock_factory.assert_not_called()

        assert len(response.products) == 2
        assert response.products[0].product_id == "first_in_db"
        assert response.products[1].product_id == "second_in_db"

    @pytest.mark.asyncio
    async def test_no_brief_brief_relevance_absent(self):
        """When brief absent, brief_relevance should not be set on products."""
        product = _make_real_product(product_id="p1")
        tenant = _make_tenant()

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product])

            response = await _get_products_impl(_make_mock_request(brief=""), _make_mock_context())

        for p in response.products:
            assert getattr(p, "brief_relevance", None) is None


# ---- Pricing suppression pipeline level: test 11 (S16, S29, S36) ----


class TestPricingSuppressionPipelineLevel:
    """Test pricing suppression for anonymous vs authenticated at pipeline level.

    BDD scenarios: T-UC-001-rule-004, T-UC-001-partition-anon-pricing,
    T-UC-001-boundary-anon-pricing (S16, S29, S36)
    """

    @pytest.mark.asyncio
    async def test_anonymous_principal_gets_empty_pricing(self):
        """Anonymous user at pipeline level gets pricing_options=[]."""
        product = _make_real_product(product_id="p1")
        tenant = _make_tenant()

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product], principal_id=None)

            response = await _get_products_impl(_make_mock_request(), _make_mock_context())

        assert len(response.products) == 1
        assert response.products[0].pricing_options == []

    @pytest.mark.asyncio
    async def test_authenticated_principal_retains_pricing(self):
        """Authenticated user at pipeline level retains full pricing_options."""
        product = _make_real_product(product_id="p1")
        tenant = _make_tenant()

        with _PipelinePatches() as mocks:
            _setup_standard_mocks(mocks, tenant, [product], principal_id="principal_1")

            response = await _get_products_impl(_make_mock_request(), _make_mock_context())

        assert len(response.products) == 1
        assert len(response.products[0].pricing_options) == 1


# ---- PricingOption XOR: test 12 (S20, S31, S38) ----


class TestPricingOptionXorNegativeCases:
    """Test PricingOption XOR validation for negative cases.

    BDD scenarios: T-UC-001-rule-006, T-UC-001-partition-pricing-xor,
    T-UC-001-boundary-pricing-xor (S20, S31, S38)
    """

    def test_both_fixed_and_floor_raises_validation_error(self):
        """PricingOption with both fixed_price AND floor_price -> validation error."""
        from pydantic import ValidationError

        from src.core.schemas import PricingOption

        with pytest.raises(ValidationError, match="Cannot have both fixed_price and floor_price"):
            PricingOption(
                pricing_option_id="bad_both",
                pricing_model="cpm",
                currency="USD",
                fixed_price=10.0,
                floor_price=5.0,
            )

    def test_neither_fixed_nor_floor_raises_validation_error(self):
        """PricingOption with neither fixed_price nor floor_price -> validation error."""
        from pydantic import ValidationError

        from src.core.schemas import PricingOption

        with pytest.raises(ValidationError, match="Must have either fixed_price"):
            PricingOption(
                pricing_option_id="bad_neither",
                pricing_model="cpm",
                currency="USD",
            )

    def test_fixed_price_only_is_valid(self):
        """PricingOption with only fixed_price is valid (positive boundary)."""
        from src.core.schemas import PricingOption

        po = PricingOption(
            pricing_option_id="good_fixed",
            pricing_model="cpm",
            currency="USD",
            fixed_price=10.0,
        )
        assert po.fixed_price == 10.0
        assert po.floor_price is None

    def test_floor_price_only_is_valid(self):
        """PricingOption with only floor_price is valid (positive boundary)."""
        from src.core.schemas import PricingOption

        po = PricingOption(
            pricing_option_id="good_floor",
            pricing_model="cpm",
            currency="USD",
            floor_price=5.0,
        )
        assert po.floor_price == 5.0
        assert po.fixed_price is None


# ---- Product conversion cardinality: test 13 (S21, S32, S39) ----


class TestProductConversionNegativeCardinality:
    """Test product_conversion rejects products with 0 format_ids, properties, or pricing.

    BDD scenarios: T-UC-001-rule-007, T-UC-001-partition-product-arrays,
    T-UC-001-boundary-product-arrays (S21, S32, S39)
    """

    def test_zero_format_ids_raises_value_error(self):
        """product_conversion with 0 format_ids -> ValueError."""
        from src.core.product_conversion import convert_product_model_to_schema

        mock_model = MagicMock()
        mock_model.product_id = "bad_product"
        mock_model.name = "Bad Product"
        mock_model.description = "No formats"
        mock_model.delivery_type = "guaranteed"
        mock_model.effective_format_ids = []  # Empty!

        with pytest.raises(ValueError, match="no format_ids"):
            convert_product_model_to_schema(mock_model)

    def test_zero_properties_raises_value_error(self):
        """product_conversion with 0 properties -> ValueError."""
        from src.core.product_conversion import convert_product_model_to_schema

        mock_model = MagicMock()
        mock_model.product_id = "bad_product"
        mock_model.name = "Bad Product"
        mock_model.description = "No properties"
        mock_model.delivery_type = "guaranteed"
        mock_model.effective_format_ids = [{"agent_url": "https://example.com", "id": "display_300x250"}]
        mock_model.effective_properties = []  # Empty!

        with pytest.raises(ValueError, match="no publisher_properties"):
            convert_product_model_to_schema(mock_model)

    def test_zero_pricing_options_raises_value_error(self):
        """product_conversion with 0 pricing_options -> ValueError."""
        from src.core.product_conversion import convert_product_model_to_schema

        mock_model = MagicMock()
        mock_model.product_id = "bad_product"
        mock_model.name = "Bad Product"
        mock_model.description = "No pricing"
        mock_model.delivery_type = "guaranteed"
        mock_model.effective_format_ids = [{"agent_url": "https://example.com", "id": "display_300x250"}]
        mock_model.effective_properties = [
            {"publisher_domain": "test.com", "selection_type": "by_tag", "property_tags": ["all"]}
        ]
        mock_model.pricing_options = []  # Empty!

        with pytest.raises(ValueError, match="no pricing_options"):
            convert_product_model_to_schema(mock_model)
