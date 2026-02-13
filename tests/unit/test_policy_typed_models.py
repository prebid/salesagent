"""Unit tests for typed model signatures in PolicyCheckService.

Verifies that check_brief_compliance accepts BrandManifest (not dict)
and check_product_eligibility accepts Product (not dict).

salesagent-9qy: Policy service should accept Pydantic models, not dicts.
"""

from unittest.mock import patch

import pytest

from src.services.policy_check_service import PolicyCheckResult, PolicyCheckService, PolicyStatus
from tests.helpers.adcp_factories import create_test_brand_manifest, create_test_product


@pytest.fixture
def policy_service():
    """Policy service with AI disabled."""
    import os

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("GEMINI_API_KEY", None)
        return PolicyCheckService(gemini_api_key=None)


class TestBriefComplianceTypedModel:
    """Test check_brief_compliance accepts BrandManifest, not dict."""

    @pytest.mark.asyncio
    async def test_accepts_brand_manifest_model(self, policy_service):
        """BrandManifest Pydantic model should be accepted directly."""
        brand = create_test_brand_manifest(name="Acme Corp", tagline="Quality widgets")

        result = await policy_service.check_brief_compliance(
            brief="Widget advertising campaign",
            brand_manifest=brand,
        )

        assert result.status == PolicyStatus.ALLOWED

    @pytest.mark.asyncio
    async def test_brand_manifest_name_used_in_context(self, policy_service):
        """Brand name from BrandManifest should be included in analysis context."""
        brand = create_test_brand_manifest(name="Acme Corp", tagline="Quality widgets since 1920")

        # Without AI, we can't inspect the context directly,
        # but we verify the call doesn't crash when accessing .name
        result = await policy_service.check_brief_compliance(
            brief="Widget advertising campaign",
            brand_manifest=brand,
        )

        assert isinstance(result, PolicyCheckResult)

    @pytest.mark.asyncio
    async def test_url_string_still_accepted(self, policy_service):
        """URL string for brand_manifest should still work."""
        result = await policy_service.check_brief_compliance(
            brief="Widget advertising campaign",
            brand_manifest="https://example.com/brand-manifest.json",
        )

        assert result.status == PolicyStatus.ALLOWED

    @pytest.mark.asyncio
    async def test_none_brand_manifest_still_works(self, policy_service):
        """None brand_manifest should still work."""
        result = await policy_service.check_brief_compliance(
            brief="Widget advertising campaign",
            brand_manifest=None,
        )

        assert result.status == PolicyStatus.ALLOWED


class TestProductEligibilityTypedModel:
    """Test check_product_eligibility accepts Product, not dict."""

    def test_accepts_product_model(self, policy_service):
        """Product Pydantic model should be accepted directly."""
        product = create_test_product(product_id="prod_1", name="Premium Display")
        policy_result = PolicyCheckResult(status=PolicyStatus.ALLOWED)

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)

        assert eligible is True
        assert reason is None

    def test_blocked_brief_rejects_product(self, policy_service):
        """Blocked policy result should reject any product."""
        product = create_test_product(product_id="prod_1", name="Premium Display")
        policy_result = PolicyCheckResult(
            status=PolicyStatus.BLOCKED,
            reason="Contains prohibited content",
        )

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)

        assert eligible is False
        assert reason == "Contains prohibited content"

    def test_restricted_brief_allows_product(self, policy_service):
        """Restricted policy result should still allow products.

        Age-based filtering is not possible because Product model does not have
        targeted_ages or verified_minimum_age fields. Products should be allowed
        unless the brief is BLOCKED.
        """
        product = create_test_product(product_id="prod_1", name="Standard Display")
        policy_result = PolicyCheckResult(
            status=PolicyStatus.RESTRICTED,
            restrictions=["Contains alcohol advertising"],
        )

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)

        assert eligible is True
        assert reason is None

    def test_restricted_with_alcohol_allows_product(self, policy_service):
        """Products should NOT be rejected for alcohol advertisers.

        The old code incorrectly rejected all products for age-restricted
        advertisers because it checked nonexistent targeted_ages/verified_minimum_age
        fields, which were always None.
        """
        product = create_test_product(product_id="prod_1", name="Adult Section Display")
        policy_result = PolicyCheckResult(
            status=PolicyStatus.RESTRICTED,
            restrictions=["Contains alcohol content"],
        )

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)

        assert eligible is True
        assert reason is None
