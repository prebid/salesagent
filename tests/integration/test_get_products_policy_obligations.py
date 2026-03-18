"""Policy enforcement obligation tests for get_products.

Tests pin down brand_manifest_policy enforcement (BR-RULE-001) and
brief policy compliance (BR-RULE-002, CONSTR-BRIEF-POLICY-01).

Uses ProductEnv harness + factories. Only mocks PolicyCheckService (LLM).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import AdCPAuthenticationError, AdCPAuthorizationError
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
# BR-RULE-001: Brand Manifest Policy Enforcement
# ---------------------------------------------------------------------------


class TestBrandManifestPolicyRequireAuth:
    """brand_manifest_policy=require_auth rejects anonymous, accepts authenticated.

    Covers: BR-RULE-001-01
    """

    @pytest.mark.asyncio
    async def test_require_auth_rejects_anonymous(self, integration_db):
        """Covers: BR-RULE-001-01

        When brand_manifest_policy=require_auth and caller is anonymous,
        request is rejected with AdCPAuthenticationError.
        """
        with ProductEnv(tenant_id="bmp-auth-anon", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="bmp-auth-anon",
                subdomain="bmp-auth-anon",
                brand_manifest_policy="require_auth",
            )
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bmp-auth-anon", principal_id=None)

            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                await env.call_impl(brief="campaign")

    @pytest.mark.asyncio
    async def test_require_auth_accepts_authenticated(self, integration_db):
        """Covers: BR-RULE-001-01

        When brand_manifest_policy=require_auth and caller is authenticated,
        request succeeds and products are returned.
        """
        with ProductEnv(tenant_id="bmp-auth-ok", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="bmp-auth-ok",
                subdomain="bmp-auth-ok",
                brand_manifest_policy="require_auth",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bmp-auth-ok", "p1")

            response = await env.call_impl(brief="campaign")

        assert len(response.products) >= 1


class TestBrandManifestPolicyRequireBrand:
    """brand_manifest_policy=require_brand rejects requests without brand.

    Covers: BR-RULE-001-01
    """

    @pytest.mark.asyncio
    async def test_require_brand_rejects_without_brand(self, integration_db):
        """Covers: BR-RULE-001-01

        When brand_manifest_policy=require_brand and no brand provided,
        request is rejected with AdCPAuthorizationError.
        """
        from src.core.schemas import GetProductsRequest as GetProductsRequestGenerated
        from src.core.tools.products import _get_products_impl

        with ProductEnv(tenant_id="bmp-brand-no", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="bmp-brand-no",
                subdomain="bmp-brand-no",
                brand_manifest_policy="require_brand",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bmp-brand-no", "p1")

            # Call _get_products_impl directly to send brand=None
            # (env.call_impl defaults brand to {"domain": "test.com"})
            req = GetProductsRequestGenerated(
                brief="campaign",
                brand=None,
                filters={"delivery_type": "guaranteed"},
            )
            with pytest.raises(AdCPAuthorizationError, match="Brand manifest required"):
                await _get_products_impl(req, env.identity)

    @pytest.mark.asyncio
    async def test_require_brand_accepts_with_brand(self, integration_db):
        """Covers: BR-RULE-001-01

        When brand_manifest_policy=require_brand and brand is provided,
        request succeeds.
        """
        with ProductEnv(tenant_id="bmp-brand-ok", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="bmp-brand-ok",
                subdomain="bmp-brand-ok",
                brand_manifest_policy="require_brand",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bmp-brand-ok", "p1")

            response = await env.call_impl(brief="campaign", brand={"domain": "nike.com"})

        assert len(response.products) >= 1


class TestBrandManifestPolicyPublic:
    """brand_manifest_policy=public allows anonymous requests without brand.

    Covers: BR-RULE-001-01
    """

    @pytest.mark.asyncio
    async def test_public_allows_anonymous(self, integration_db):
        """Covers: BR-RULE-001-01

        When brand_manifest_policy=public and caller is anonymous,
        request succeeds and products are returned.
        """
        with ProductEnv(tenant_id="bmp-pub-anon", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="bmp-pub-anon",
                subdomain="bmp-pub-anon",
                brand_manifest_policy="public",
            )
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bmp-pub-anon", principal_id=None)

            response = await env.call_impl(brief="campaign")

        assert len(response.products) >= 1


class TestBrandManifestPolicyDefault:
    """Default (no policy set) behaves as require_auth.

    Covers: BR-RULE-001-01
    """

    @pytest.mark.asyncio
    async def test_default_policy_rejects_anonymous(self, integration_db):
        """Covers: BR-RULE-001-01

        When no brand_manifest_policy is explicitly set, the default
        require_auth behavior rejects anonymous requests.
        """
        with ProductEnv(tenant_id="bmp-def-anon", principal_id=None) as env:
            # Do not set brand_manifest_policy -- rely on DB default "require_auth"
            tenant = TenantFactory(
                tenant_id="bmp-def-anon",
                subdomain="bmp-def-anon",
            )
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bmp-def-anon", principal_id=None)

            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                await env.call_impl(brief="campaign")


# ---------------------------------------------------------------------------
# BR-RULE-002: Brief Policy Compliance
# ---------------------------------------------------------------------------


class TestBriefPolicyBlocked:
    """advertising_policy enabled + BLOCKED brief rejects request.

    Covers: BR-RULE-002-01
    """

    @pytest.mark.asyncio
    async def test_blocked_brief_raises_policy_violation(self, integration_db):
        """Covers: BR-RULE-002-01

        When advertising_policy is enabled and LLM returns BLOCKED,
        request is rejected with POLICY_VIOLATION error code.
        """
        with ProductEnv(tenant_id="bp-blocked", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="bp-blocked",
                subdomain="bp-blocked",
                advertising_policy={"enabled": True},
                gemini_api_key="test-key",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bp-blocked", "p1")

            policy_result = PolicyCheckResult(
                status=PolicyStatus.BLOCKED,
                reason="Prohibited content: gambling",
            )
            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(return_value=policy_result)
            env.mock["policy_service"].return_value = mock_policy_inst

            with pytest.raises(AdCPAuthorizationError) as exc_info:
                await env.call_impl(brief="Online gambling ads")

        assert exc_info.value.details.get("error_code") == "POLICY_VIOLATION"


class TestBriefPolicyRestrictedManualReview:
    """advertising_policy enabled + RESTRICTED + manual_review rejects request.

    Covers: BR-RULE-002-01
    """

    @pytest.mark.asyncio
    async def test_restricted_with_manual_review_raises_policy_violation(self, integration_db):
        """Covers: BR-RULE-002-01

        When advertising_policy is enabled with require_manual_review=True
        and LLM returns RESTRICTED, request is rejected with POLICY_VIOLATION.
        """
        with ProductEnv(tenant_id="bp-restrict", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="bp-restrict",
                subdomain="bp-restrict",
                advertising_policy={"enabled": True, "require_manual_review": True},
                gemini_api_key="test-key",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bp-restrict", "p1")

            policy_result = PolicyCheckResult(
                status=PolicyStatus.RESTRICTED,
                reason="Content may violate alcohol advertising guidelines",
                restrictions=["alcohol_marketing"],
            )
            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(return_value=policy_result)
            env.mock["policy_service"].return_value = mock_policy_inst

            with pytest.raises(AdCPAuthorizationError) as exc_info:
                await env.call_impl(brief="Craft beer festival promotion")

        assert exc_info.value.details.get("error_code") == "POLICY_VIOLATION"


class TestBriefPolicyApproved:
    """advertising_policy enabled + APPROVED brief succeeds.

    Covers: BR-RULE-002-01
    """

    @pytest.mark.asyncio
    async def test_approved_brief_returns_products(self, integration_db):
        """Covers: BR-RULE-002-01

        When advertising_policy is enabled and LLM returns ALLOWED,
        request succeeds and products are returned.
        """
        with ProductEnv(tenant_id="bp-approved", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="bp-approved",
                subdomain="bp-approved",
                advertising_policy={"enabled": True},
                gemini_api_key="test-key",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bp-approved", "p1")

            policy_result = PolicyCheckResult(
                status=PolicyStatus.ALLOWED,
                reason="Brief complies with all policies",
            )
            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(return_value=policy_result)
            mock_policy_inst.check_product_eligibility.return_value = (True, None)
            env.mock["policy_service"].return_value = mock_policy_inst

            response = await env.call_impl(brief="Athletic footwear campaign")

        assert len(response.products) >= 1


class TestBriefPolicyServiceUnavailable:
    """advertising_policy enabled + LLM service unavailable fails open.

    Covers: BR-RULE-002-01, CONSTR-BRIEF-POLICY-01
    """

    @pytest.mark.asyncio
    async def test_service_unavailable_fails_open(self, integration_db):
        """Covers: BR-RULE-002-01, CONSTR-BRIEF-POLICY-01

        When advertising_policy is enabled but the LLM policy service raises
        an exception, the system fails open and products are still returned.
        """
        with ProductEnv(tenant_id="bp-failopen", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="bp-failopen",
                subdomain="bp-failopen",
                advertising_policy={"enabled": True},
                gemini_api_key="test-key",
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bp-failopen", "p1")

            mock_policy_inst = MagicMock()
            mock_policy_inst.check_brief_compliance = AsyncMock(side_effect=RuntimeError("Gemini API timeout"))
            env.mock["policy_service"].return_value = mock_policy_inst

            response = await env.call_impl(brief="Normal campaign brief")

        assert len(response.products) >= 1
        assert response.products[0].product_id == "p1"


class TestBriefPolicyDisabled:
    """advertising_policy disabled skips brief policy check entirely.

    Covers: CONSTR-BRIEF-POLICY-01
    """

    @pytest.mark.asyncio
    async def test_policy_disabled_skips_check(self, integration_db):
        """Covers: CONSTR-BRIEF-POLICY-01

        When advertising_policy is not enabled, no policy check is performed
        and products are returned normally regardless of brief content.
        """
        with ProductEnv(tenant_id="bp-disabled", principal_id="p1") as env:
            tenant = TenantFactory(
                tenant_id="bp-disabled",
                subdomain="bp-disabled",
                # advertising_policy not set or disabled
            )
            PrincipalFactory(tenant=tenant, principal_id="p1")
            p = ProductFactory(tenant=tenant, product_id="p1")
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("bp-disabled", "p1")

            response = await env.call_impl(brief="Any brief content")

            assert len(response.products) >= 1
            # PolicyCheckService should not have been instantiated
            env.mock["policy_service"].assert_not_called()
