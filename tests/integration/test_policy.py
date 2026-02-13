"""Tests for policy check functionality."""

from unittest.mock import patch

import pytest
from adcp import GetProductsRequest

from src.services.policy_check_service import PolicyCheckResult, PolicyCheckService, PolicyStatus
from tests.helpers.adcp_factories import create_test_brand_manifest, create_test_product

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def policy_service():
    """Create a policy service without API key for basic testing."""
    # Service without AI will just allow everything with a warning
    # Must clear GEMINI_API_KEY env var to ensure AI is truly disabled
    with patch.dict("os.environ", {}, clear=False):
        # Remove GEMINI_API_KEY if present
        import os

        os.environ.pop("GEMINI_API_KEY", None)
        return PolicyCheckService(gemini_api_key=None)


@pytest.fixture
def policy_service_with_ai():
    """Create a policy service with mocked AI via Pydantic AI."""
    # The service needs AI enabled. We'll patch the check_policy_compliance function
    # to return mock responses in each test.
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        service = PolicyCheckService()
        return service


class TestPolicyWithoutAI:
    """Test policy service behavior without AI."""

    @pytest.mark.asyncio
    async def test_no_ai_returns_allowed_with_warning(self, policy_service):
        """Test that without AI, all content is allowed with a warning."""
        test_cases = ["Target children with candy ads", "Cannabis delivery service", "New smartphone launch"]

        for brief in test_cases:
            result = await policy_service.check_brief_compliance(brief)
            assert result.status == PolicyStatus.ALLOWED
            assert len(result.warnings) > 0
            # Check for either message format - service might say "not configured" or "unavailable"
            assert any(
                msg in result.warnings[0]
                for msg in ["AI service not configured", "AI policy check unavailable", "Policy check unavailable"]
            )


class TestAIPolicyAnalysis:
    """Test AI-powered policy analysis."""

    @pytest.mark.asyncio
    async def test_ai_blocks_subtle_violations(self, policy_service_with_ai):
        """Test that AI catches subtle policy violations."""
        from src.services.ai.agents.policy_agent import PolicyAnalysis

        # Mock the check_policy_compliance function to return a blocked response
        mock_analysis = PolicyAnalysis(
            status="blocked",
            reason="Targets vulnerable elderly population with predatory financial services",
            restrictions=[],
            warnings=[],
        )

        with patch("src.services.policy_check_service.check_policy_compliance", return_value=mock_analysis):
            result = await policy_service_with_ai.check_brief_compliance("Reverse mortgage solutions for seniors")

        assert result.status == PolicyStatus.BLOCKED
        assert "elderly population" in result.reason

    @pytest.mark.asyncio
    async def test_ai_with_tenant_policies(self, policy_service_with_ai):
        """Test AI respects tenant-specific policies."""
        from src.services.ai.agents.policy_agent import PolicyAnalysis

        tenant_policies = {
            "custom_rules": {
                "prohibited_advertisers": ["badcompany.com"],
                "prohibited_categories": ["competitor_products"],
                "prohibited_tactics": ["comparative advertising"],
            }
        }

        # Mock the check_policy_compliance function to return a blocked response
        mock_analysis = PolicyAnalysis(
            status="blocked",
            reason="Contains prohibited advertiser: badcompany.com",
            restrictions=[],
            warnings=[],
        )

        with patch("src.services.policy_check_service.check_policy_compliance", return_value=mock_analysis):
            result = await policy_service_with_ai.check_brief_compliance(
                "Compare our product to competitor_brand", tenant_policies=tenant_policies
            )

        assert result.status == PolicyStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_ai_fallback_on_error(self, policy_service_with_ai):
        """Test graceful fallback when AI fails."""
        from src.services.ai.agents.policy_agent import PolicyAnalysis

        # Mock the check_policy_compliance function to return allowed with warning (graceful fallback)
        # The policy_agent itself handles exceptions and returns allowed with warning
        mock_analysis = PolicyAnalysis(
            status="allowed",
            warnings=["AI policy check unavailable: API error"],
        )

        with patch("src.services.policy_check_service.check_policy_compliance", return_value=mock_analysis):
            result = await policy_service_with_ai.check_brief_compliance("Normal product advertisement")

        # When AI fails, it should still return allowed with warning
        assert result.status == PolicyStatus.ALLOWED
        assert len(result.warnings) > 0
        assert "AI policy check unavailable" in result.warnings[0]


class TestProductEligibility:
    """Test product eligibility based on policy results."""

    def test_blocked_brief_no_products(self, policy_service):
        """Test that blocked briefs can't use any products."""
        policy_result = PolicyCheckResult(status=PolicyStatus.BLOCKED, reason="Contains prohibited content")

        product = create_test_product(product_id="prod_1", name="Premium Display")

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)
        assert not eligible
        assert reason == "Contains prohibited content"

    def test_restricted_brief_allows_products(self, policy_service):
        """Test that restricted briefs still allow products."""
        policy_result = PolicyCheckResult(status=PolicyStatus.RESTRICTED, restrictions=["Contains alcohol content"])

        product = create_test_product(product_id="prod_1", name="Standard Display")

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)
        assert eligible
        assert reason is None

    def test_allowed_brief_eligible_product(self, policy_service):
        """Test that allowed briefs can use products."""
        policy_result = PolicyCheckResult(status=PolicyStatus.ALLOWED)

        product = create_test_product(product_id="prod_1", name="Standard Display")

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)
        assert eligible
        assert reason is None


class TestIntegration:
    """Test integration with get_products endpoint."""

    @pytest.mark.asyncio
    async def test_brand_manifest_included(self, policy_service):
        """Test that brand_manifest is included in analysis."""
        brand = create_test_brand_manifest(name="Weight loss pills - Lose 30 pounds in 30 days guaranteed!")

        result = await policy_service.check_brief_compliance(
            brief="Advertisement for wellness products",
            brand_manifest=brand,
        )

        # Without AI, should be allowed with warning
        assert result.status == PolicyStatus.ALLOWED
        assert len(result.warnings) > 0

    @pytest.mark.asyncio
    async def test_policy_result_timestamp(self, policy_service):
        """Test that policy results include timestamp."""
        result = await policy_service.check_brief_compliance("Test brief")

        assert result.timestamp is not None
        assert hasattr(result.timestamp, "isoformat")  # It's a datetime


@pytest.mark.asyncio
async def test_full_request_flow():
    """Test the full request flow with policy checking."""
    # This would be an integration test with the actual endpoint
    request = GetProductsRequest(
        brief="Looking to advertise a new smartphone",
        brand_manifest={"name": "TechCorp - Latest 5G smartphone with advanced features"},
    )

    # Verify the request has brand_manifest (AdCP v2.2.0 spec field)
    assert hasattr(request, "brand_manifest")
    # Library may wrap in BrandManifestReference with BrandManifest in root
    expected_name = "TechCorp - Latest 5G smartphone with advanced features"
    if isinstance(request.brand_manifest, dict):
        assert request.brand_manifest["name"] == expected_name
    elif hasattr(request.brand_manifest, "name"):
        assert request.brand_manifest.name == expected_name
    elif hasattr(request.brand_manifest, "root") and hasattr(request.brand_manifest.root, "name"):
        assert request.brand_manifest.root.name == expected_name
