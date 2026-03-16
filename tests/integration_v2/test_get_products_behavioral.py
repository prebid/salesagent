"""Behavioral integration tests for get_products (UC-001).

MIGRATED from tests/unit/test_get_products_behavioral.py.
Uses ProductEnv harness + factories instead of mocked unit tests.

These tests pin down _get_products_impl behavior before FastAPI migration.
Each test is traced to a BDD scenario from BR-UC-001-discover-available-inventory.feature.

Tests are ordered by migration risk: HIGH_RISK first, then MEDIUM_RISK.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import AdCPAuthorizationError, AdCPError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.tenant_context import LazyTenantContext
from src.core.testing_hooks import AdCPTestContext
from src.services.policy_check_service import PolicyCheckResult, PolicyStatus
from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _lazy_identity(
    tenant_id: str,
    principal_id: str | None = "p1",
) -> ResolvedIdentity:
    """Create a ResolvedIdentity using LazyTenantContext for real DB tenant lookup."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=LazyTenantContext(tenant_id),
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
    )


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
    async def test_ranking_sorts_descending_and_filters_below_threshold(self, integration_db):
        """When brief provided + AI scores products, threshold >= 0.1 applied, sorted descending."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        with ProductEnv(tenant_id="rank-sort", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="rank-sort",
                subdomain="rank-sort",
                product_ranking_prompt="Rank by relevance to sports",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")

            for pid in ("p_high", "p_med", "p_low", "p_excluded"):
                p = ProductFactory(tenant=tenant, product_id=pid)
                PricingOptionFactory(product=p)

            ranking_result = ProductRankingResult(
                rankings=[
                    ProductRanking(product_id="p_high", relevance_score=0.9, reason="Very relevant"),
                    ProductRanking(product_id="p_med", relevance_score=0.5, reason="Somewhat relevant"),
                    ProductRanking(product_id="p_low", relevance_score=0.1, reason="Barely relevant"),
                    ProductRanking(product_id="p_excluded", relevance_score=0.09, reason="Not relevant"),
                ]
            )

            # Use lazy identity so pipeline reads tenant config from DB
            env._identity = _lazy_identity("rank-sort", "p1")

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

                response = await env.call_impl(brief="sports equipment campaign")

        assert len(response.products) == 3
        assert response.products[0].product_id == "p_high"
        assert response.products[1].product_id == "p_med"
        assert response.products[2].product_id == "p_low"

    @pytest.mark.asyncio
    async def test_ranking_boundary_score_0_1_included(self, integration_db):
        """Score exactly 0.1 should be INCLUDED (>= 0.1 threshold)."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        with ProductEnv(tenant_id="rank-incl", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="rank-incl",
                subdomain="rank-incl",
                product_ranking_prompt="Rank products",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p_boundary")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("rank-incl", "p1")

            ranking_result = ProductRankingResult(
                rankings=[ProductRanking(product_id="p_boundary", relevance_score=0.1, reason="Boundary")]
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

                env.mock["ranking_factory"].stop()

                response = await env.call_impl(brief="campaign")

        assert len(response.products) == 1
        assert response.products[0].product_id == "p_boundary"

    @pytest.mark.asyncio
    async def test_ranking_boundary_score_0_09_excluded(self, integration_db):
        """Score 0.09 should be EXCLUDED (< 0.1 threshold)."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        with ProductEnv(tenant_id="rank-excl", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="rank-excl",
                subdomain="rank-excl",
                product_ranking_prompt="Rank products",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p_below")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("rank-excl", "p1")

            ranking_result = ProductRankingResult(
                rankings=[ProductRanking(product_id="p_below", relevance_score=0.09, reason="Below")]
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

                env.mock["ranking_factory"].stop()

                response = await env.call_impl(brief="campaign")

        assert len(response.products) == 0

    @pytest.mark.asyncio
    async def test_brief_relevance_not_set_on_products(self, integration_db):
        """brief_relevance is NOT_IMPLEMENTED -- field should be absent/None after ranking."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        with ProductEnv(tenant_id="rank-rel", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="rank-rel",
                subdomain="rank-rel",
                product_ranking_prompt="Rank products",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("rank-rel", "p1")

            ranking_result = ProductRankingResult(
                rankings=[ProductRanking(product_id="p1", relevance_score=0.8, reason="Good")]
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

                env.mock["ranking_factory"].stop()

                response = await env.call_impl(brief="campaign")

        for p in response.products:
            assert getattr(p, "brief_relevance", None) is None, (
                "brief_relevance should NOT be set by _get_products_impl (NOT_IMPLEMENTED)"
            )


class TestRankingFailureFailopen:
    """Test AI ranking failure results in fail-open behavior.

    BDD scenario: T-UC-001-rule-005-fail (S19)
    """

    @pytest.mark.asyncio
    async def test_ranking_failure_returns_products_unranked(self, integration_db):
        """When AI ranking service raises Exception, products returned in catalog order."""
        with ProductEnv(tenant_id="rank-fail", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="rank-fail",
                subdomain="rank-fail",
                product_ranking_prompt="Rank products",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p1 = ProductFactory(tenant=tenant, product_id="p_first")
            PricingOptionFactory(product=p1)
            p2 = ProductFactory(tenant=tenant, product_id="p_second")
            PricingOptionFactory(product=p2)

            env._identity = _lazy_identity("rank-fail", "p1")

            with patch("src.services.ai.factory.get_factory") as mock_factory:
                factory_inst = MagicMock()
                factory_inst.is_ai_enabled.return_value = True
                factory_inst.create_model.side_effect = RuntimeError("AI service unavailable")
                mock_factory.return_value = factory_inst

                env.mock["ranking_factory"].stop()

                response = await env.call_impl(brief="campaign")

        assert len(response.products) == 2
        assert response.products[0].product_id == "p_first"
        assert response.products[1].product_id == "p_second"


# ---- Policy: tests 3, 4, 5 (S9, S8, S10) ----


class TestPolicyBlockedPipelineRejection:
    """Test BLOCKED policy raises error through _get_products_impl pipeline.

    BDD scenario: T-UC-001-ext-a-blocked (S8)
    """

    @pytest.mark.asyncio
    async def test_blocked_policy_raises_tool_error(self, integration_db):
        """When policy returns BLOCKED, AdCPAuthorizationError('POLICY_VIOLATION') raised."""
        with ProductEnv(tenant_id="pol-blocked", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="pol-blocked",
                subdomain="pol-blocked",
                advertising_policy={"enabled": True},
                gemini_api_key="test-key",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("pol-blocked", "p1")

            policy_result = PolicyCheckResult(
                status=PolicyStatus.BLOCKED,
                reason="Prohibited content: gambling",
            )

            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(return_value=policy_result)
            env.mock["policy_service"].return_value = mock_policy_inst

            with pytest.raises(AdCPAuthorizationError) as exc_info:
                await env.call_impl(brief="Online gambling")

        assert exc_info.value.details.get("error_code") == "POLICY_VIOLATION"
        assert "gambling" in str(exc_info.value).lower()


class TestRestrictedBriefManualReviewRejection:
    """Test RESTRICTED + require_manual_review raises error.

    BDD scenario: T-UC-001-ext-a-restricted (S9)
    """

    @pytest.mark.asyncio
    async def test_restricted_with_manual_review_raises_tool_error(self, integration_db):
        """When RESTRICTED + require_manual_review=True, AdCPAuthorizationError raised."""
        with ProductEnv(tenant_id="pol-restrict", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="pol-restrict",
                subdomain="pol-restrict",
                advertising_policy={"enabled": True, "require_manual_review": True},
                gemini_api_key="test-key",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("pol-restrict", "p1")

            policy_result = PolicyCheckResult(
                status=PolicyStatus.RESTRICTED,
                reason="Content may violate alcohol advertising guidelines",
                restrictions=["alcohol_marketing"],
            )

            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(return_value=policy_result)
            env.mock["policy_service"].return_value = mock_policy_inst

            with pytest.raises(AdCPAuthorizationError) as exc_info:
                await env.call_impl(brief="Craft beer festival")

        assert exc_info.value.details.get("error_code") == "POLICY_VIOLATION"
        assert "alcohol" in str(exc_info.value).lower()


class TestPolicyServiceFailopenPipeline:
    """Test policy service exception results in fail-open behavior.

    BDD scenario: T-UC-001-ext-a-failopen (S10)
    """

    @pytest.mark.asyncio
    async def test_policy_exception_returns_products_normally(self, integration_db):
        """When PolicyCheckService.check_brief_compliance raises, products still returned."""
        with ProductEnv(tenant_id="pol-failopen", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="pol-failopen",
                subdomain="pol-failopen",
                advertising_policy={"enabled": True},
                gemini_api_key="test-key",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("pol-failopen", "p1")

            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(side_effect=RuntimeError("Gemini API timeout"))
            env.mock["policy_service"].return_value = mock_policy_inst

            response = await env.call_impl(brief="Normal campaign")

        assert len(response.products) == 1
        assert response.products[0].product_id == "p1"


# ---- Adapter annotation: test 6 (S25) ----


class TestAdapterSupportAnnotation:
    """Test pricing options annotated with adapter support info.

    BDD scenario: T-UC-001-adapter (S25)
    """

    @pytest.mark.asyncio
    async def test_supported_pricing_annotated(self, integration_db):
        """When adapter supports pricing model, supported=True is set on inner."""
        with ProductEnv(tenant_id="adpt-support", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="adpt-support", subdomain="adpt-support")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p, pricing_model="cpm")

            with patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter:
                mock_adapter = MagicMock()
                mock_adapter.get_supported_pricing_models.return_value = {"cpm", "cpc"}
                mock_get_adapter.return_value = mock_adapter

                response = await env.call_impl(brief="campaign")

        assert len(response.products) == 1
        assert len(response.products[0].pricing_options) == 1
        inner = response.products[0].pricing_options[0].root
        assert inner.supported is True

    @pytest.mark.asyncio
    async def test_unsupported_pricing_annotated_with_reason(self, integration_db):
        """When adapter does NOT support pricing model, unsupported_reason is set."""
        with ProductEnv(tenant_id="adpt-unsup", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="adpt-unsup", subdomain="adpt-unsup")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p, pricing_model="vcpm", rate=Decimal("15.00"))

            with patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter:
                mock_adapter = MagicMock()
                mock_adapter.get_supported_pricing_models.return_value = {"cpm"}
                mock_get_adapter.return_value = mock_adapter

                response = await env.call_impl(brief="campaign")

        inner = response.products[0].pricing_options[0].root
        assert inner.supported is False
        assert "VCPM" in inner.unsupported_reason


# ---- Empty results pipeline stages: test 7 (S5) ----


class TestEmptyResultsPipelineStages:
    """Test that each pipeline stage can produce empty results.

    BDD scenario: T-UC-001-alt-empty-causes (S5)
    """

    @pytest.mark.asyncio
    async def test_empty_catalog_returns_empty(self, integration_db):
        """When no products exist in DB, empty products returned."""
        with ProductEnv(tenant_id="empty-cat", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="empty-cat", subdomain="empty-cat")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            # No products created -- catalog is empty

            response = await env.call_impl(brief="Athletic footwear")

        assert response.products == []

    @pytest.mark.asyncio
    async def test_access_control_filters_all_returns_empty(self, integration_db):
        """When all products are restricted and user is anonymous, empty returned."""
        with ProductEnv(tenant_id="acl-empty", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="acl-empty",
                subdomain="acl-empty",
                brand_manifest_policy="public",
            )
            p = ProductFactory(
                tenant=tenant,
                product_id="restricted_p",
                allowed_principal_ids=["other_principal"],
            )
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("acl-empty", principal_id=None)

            response = await env.call_impl(brief="Athletic footwear")

        assert response.products == []

    @pytest.mark.asyncio
    async def test_filter_mismatch_returns_empty(self, integration_db):
        """When delivery_type filter matches nothing, empty returned."""
        with ProductEnv(tenant_id="filt-empty", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="filt-empty", subdomain="filt-empty")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1", delivery_type="guaranteed")
            PricingOptionFactory(product=p)

            response = await env.call_impl(
                brief="Athletic footwear",
                filters={"delivery_type": "non_guaranteed"},
            )

        assert response.products == []

    @pytest.mark.asyncio
    async def test_ranking_threshold_eliminates_all_returns_empty(self, integration_db):
        """When ranking scores are all below threshold, empty returned."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        with ProductEnv(tenant_id="rank-elim", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="rank-elim",
                subdomain="rank-elim",
                product_ranking_prompt="Rank products",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("rank-elim", "p1")

            ranking_result = ProductRankingResult(
                rankings=[ProductRanking(product_id="p1", relevance_score=0.05, reason="Not relevant")]
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

                env.mock["ranking_factory"].stop()

                response = await env.call_impl(brief="unrelated brief")

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
            (True, True, None, PolicyStatus.BLOCKED, False, True, "POLICY_VIOLATION"),
            (True, True, None, PolicyStatus.RESTRICTED, True, True, "POLICY_VIOLATION"),
            (True, True, None, PolicyStatus.RESTRICTED, False, False, None),
            (True, True, None, PolicyStatus.ALLOWED, False, False, None),
            (False, True, None, None, False, False, None),
            (True, False, None, None, False, False, None),
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
        integration_db,
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

        tenant_kwargs = {"advertising_policy": adv_policy}
        if has_api_key:
            tenant_kwargs["gemini_api_key"] = "test-key"

        # Unique tenant_id per parametrize row
        row_suffix = f"{policy_enabled}-{has_api_key}-{policy_status}-{require_manual_review}"
        row_id = f"pm-{hash(row_suffix) % 10000:04d}"

        with ProductEnv(tenant_id=row_id, principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id=row_id,
                subdomain=row_id,
                **tenant_kwargs,
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity(row_id, "p1")

            mock_policy_result = None
            if policy_status is not None:
                mock_policy_result = PolicyCheckResult(
                    status=policy_status,
                    reason="Test reason",
                    restrictions=["test_restriction"],
                )

            mock_policy_inst = MagicMock()
            if policy_side_effect:
                mock_policy_inst.check_brief_compliance = AsyncMock(side_effect=policy_side_effect)
            elif mock_policy_result:
                mock_policy_inst.check_brief_compliance = AsyncMock(return_value=mock_policy_result)
                mock_policy_inst.check_product_eligibility.return_value = (True, None)
            env.mock["policy_service"].return_value = mock_policy_inst

            if expect_error:
                with pytest.raises(AdCPError) as exc_info:
                    await env.call_impl(brief="test")
                error_str = str(exc_info.value)
                details = getattr(exc_info.value, "details", {}) or {}
                assert error_substring in error_str or details.get("error_code") == error_substring
            else:
                response = await env.call_impl(brief="test")
                assert response is not None


# ---- No brief skips ranking: test 10 (S2) ----


class TestNoBriefSkipsRanking:
    """Test that absent brief skips ranking and returns catalog order.

    BDD scenario: T-UC-001-alt-no-brief (S2)
    """

    @pytest.mark.asyncio
    async def test_no_brief_returns_catalog_order(self, integration_db):
        """When brief is empty, ranking skipped, products returned in DB order."""
        with ProductEnv(tenant_id="no-brief", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="no-brief",
                subdomain="no-brief",
                product_ranking_prompt="Rank products",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p1 = ProductFactory(tenant=tenant, product_id="first_in_db")
            PricingOptionFactory(product=p1)
            p2 = ProductFactory(tenant=tenant, product_id="second_in_db")
            PricingOptionFactory(product=p2)

            with patch("src.services.ai.factory.get_factory") as mock_factory:
                response = await env.call_impl(brief="")
                mock_factory.assert_not_called()

        assert len(response.products) == 2
        assert response.products[0].product_id == "first_in_db"
        assert response.products[1].product_id == "second_in_db"

    @pytest.mark.asyncio
    async def test_no_brief_brief_relevance_absent(self, integration_db):
        """When brief absent, brief_relevance should not be set on products."""
        with ProductEnv(tenant_id="no-brief-rel", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="no-brief-rel", subdomain="no-brief-rel")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            response = await env.call_impl(brief="")

        for p in response.products:
            assert getattr(p, "brief_relevance", None) is None


# ---- Pricing suppression pipeline level: test 11 (S16, S29, S36) ----


class TestPricingSuppressionPipelineLevel:
    """Test pricing suppression for anonymous vs authenticated at pipeline level.

    BDD scenarios: T-UC-001-rule-004, T-UC-001-partition-anon-pricing,
    T-UC-001-boundary-anon-pricing (S16, S29, S36)
    """

    @pytest.mark.asyncio
    async def test_anonymous_principal_gets_empty_pricing(self, integration_db):
        """Anonymous user at pipeline level gets pricing_options=[]."""
        with ProductEnv(tenant_id="anon-price", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="anon-price",
                subdomain="anon-price",
                brand_manifest_policy="public",
            )
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("anon-price", principal_id=None)

            response = await env.call_impl(brief="Athletic footwear")

        assert len(response.products) == 1
        assert response.products[0].pricing_options == []

    @pytest.mark.asyncio
    async def test_authenticated_principal_retains_pricing(self, integration_db):
        """Authenticated user at pipeline level retains full pricing_options."""
        with ProductEnv(tenant_id="auth-price", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="auth-price", subdomain="auth-price")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            response = await env.call_impl(brief="Athletic footwear")

        assert len(response.products) == 1
        assert len(response.products[0].pricing_options) == 1


# ---- PricingOption XOR: test 12 (S20, S31, S38) ----


class TestPricingOptionXorNegativeCases:
    """Test PricingOption XOR validation for negative cases.

    BDD scenarios: T-UC-001-rule-006, T-UC-001-partition-pricing-xor,
    T-UC-001-boundary-pricing-xor (S20, S31, S38)

    Note: These are schema validation tests -- no DB needed, kept for suite cohesion.
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

    Note: These test convert_product_model_to_schema directly with mock models.
    """

    def test_zero_format_ids_raises_value_error(self):
        """product_conversion with 0 format_ids -> ValueError."""
        from src.core.product_conversion import convert_product_model_to_schema

        mock_model = MagicMock()
        mock_model.product_id = "bad_product"
        mock_model.name = "Bad Product"
        mock_model.description = "No formats"
        mock_model.delivery_type = "guaranteed"
        mock_model.effective_format_ids = []

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
        mock_model.effective_properties = []

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
        mock_model.pricing_options = []

        with pytest.raises(ValueError, match="no pricing_options"):
            convert_product_model_to_schema(mock_model)


# ---------------------------------------------------------------------------
# Search criteria validation (salesagent-k13e)
# ---------------------------------------------------------------------------


class TestSearchCriteriaValidation:
    """_get_products_impl requires at least one search criterion (brief, brand, or filters).

    Enforced uniformly across all transports (MCP, A2A, REST).

    Note: These tests call _get_products_impl directly because ProductEnv.call_impl
    always provides a default brand={"domain": "test.com"}, which would satisfy the
    search criteria requirement. Direct calls are needed to test with brand=None.
    """

    @pytest.mark.asyncio
    async def test_no_search_criteria_raises_validation_error(self, integration_db):
        """When brief, brand, and filters are all empty/None, _impl raises AdCPValidationError."""
        from src.core.schemas import GetProductsRequest as GetProductsRequestGenerated
        from src.core.tools.products import _get_products_impl

        with ProductEnv(tenant_id="no-crit", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="no-crit", subdomain="no-crit")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            req = GetProductsRequestGenerated(brief=None, brand=None, filters=None)
            with pytest.raises(AdCPValidationError, match="brief.*brand.*filters"):
                await _get_products_impl(req, env.identity)

    @pytest.mark.asyncio
    async def test_empty_string_brief_counts_as_no_criteria(self, integration_db):
        """An empty string brief is equivalent to None for search criteria validation."""
        from src.core.schemas import GetProductsRequest as GetProductsRequestGenerated
        from src.core.tools.products import _get_products_impl

        with ProductEnv(tenant_id="empty-br", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="empty-br", subdomain="empty-br")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            req = GetProductsRequestGenerated(brief="", brand=None, filters=None)
            with pytest.raises(AdCPValidationError, match="brief.*brand.*filters"):
                await _get_products_impl(req, env.identity)

    @pytest.mark.asyncio
    async def test_brief_alone_satisfies_search_criteria(self, integration_db):
        """A non-empty brief is sufficient search criteria."""
        from src.core.schemas import GetProductsRequest as GetProductsRequestGenerated
        from src.core.tools.products import _get_products_impl

        with ProductEnv(tenant_id="brief-ok", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="brief-ok", subdomain="brief-ok")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            req = GetProductsRequestGenerated(brief="Athletic footwear", brand=None, filters=None)
            response = await _get_products_impl(req, env.identity)

        assert response.products is not None

    @pytest.mark.asyncio
    async def test_brand_alone_satisfies_search_criteria(self, integration_db):
        """A brand reference is sufficient search criteria."""
        with ProductEnv(tenant_id="brand-ok", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="brand-ok", subdomain="brand-ok")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            response = await env.call_impl(brief=None, brand={"domain": "nike.com"})

        assert response.products is not None

    @pytest.mark.asyncio
    async def test_filters_alone_satisfies_search_criteria(self, integration_db):
        """A filters object is sufficient search criteria."""
        with ProductEnv(tenant_id="filt-ok", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="filt-ok", subdomain="filt-ok")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            response = await env.call_impl(brief=None, filters={})

        assert response.products is not None

    @pytest.mark.asyncio
    async def test_validation_error_has_correct_error_code(self, integration_db):
        """AdCPValidationError has error_code='VALIDATION_ERROR'."""
        from src.core.schemas import GetProductsRequest as GetProductsRequestGenerated
        from src.core.tools.products import _get_products_impl

        with ProductEnv(tenant_id="err-code", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="err-code", subdomain="err-code")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            req = GetProductsRequestGenerated(brief=None, brand=None, filters=None)
            with pytest.raises(AdCPValidationError) as exc_info:
                await _get_products_impl(req, env.identity)

        assert exc_info.value.error_code == "VALIDATION_ERROR"
