"""Tests for policy check functionality."""

from unittest.mock import Mock, patch

import pytest

from src.core.schema_adapters import GetProductsRequest
from src.services.policy_check_service import PolicyCheckResult, PolicyCheckService, PolicyStatus

pytestmark = pytest.mark.integration


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
    """Create a policy service with mocked AI."""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        service = PolicyCheckService()
        # Mock the AI model with synchronous mock that returns the right structure
        service.model = Mock()
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

        # Mock AI response
        # Create a coroutine that returns a mock response
        async def mock_generate(*args, **kwargs):
            mock_response = Mock()
            mock_response.text = '{"status": "blocked", "reason": "Targets vulnerable elderly population with predatory financial services", "restrictions": [], "warnings": []}'
            return mock_response

        policy_service_with_ai.model.generate_content_async = mock_generate

        result = await policy_service_with_ai.check_brief_compliance("Reverse mortgage solutions for seniors")

        assert result.status == PolicyStatus.BLOCKED
        assert "elderly population" in result.reason

    @pytest.mark.asyncio
    async def test_ai_with_tenant_policies(self, policy_service_with_ai):
        """Test AI respects tenant-specific policies."""
        tenant_policies = {
            "custom_rules": {
                "prohibited_advertisers": ["badcompany.com"],
                "prohibited_categories": ["competitor_products"],
                "prohibited_tactics": ["comparative advertising"],
            }
        }

        # Create a coroutine that returns a mock response
        async def mock_generate(*args, **kwargs):
            mock_response = Mock()
            mock_response.text = '{"status": "blocked", "reason": "Contains prohibited advertiser: badcompany.com", "restrictions": [], "warnings": []}'
            return mock_response

        policy_service_with_ai.model.generate_content_async = mock_generate

        result = await policy_service_with_ai.check_brief_compliance(
            "Compare our product to competitor_brand", tenant_policies=tenant_policies
        )

        assert result.status == PolicyStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_ai_fallback_on_error(self, policy_service_with_ai):
        """Test graceful fallback when AI fails."""

        # Create a coroutine that raises an exception
        async def mock_generate_error(*args, **kwargs):
            raise Exception("API error")

        policy_service_with_ai.model.generate_content_async = mock_generate_error

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

        product = {"product_id": "prod_1", "name": "Premium Display"}

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)
        assert not eligible
        assert reason == "Contains prohibited content"

    def test_alcohol_advertiser_age_restrictions(self, policy_service):
        """Test that alcohol advertisers can't use children's content."""
        policy_result = PolicyCheckResult(status=PolicyStatus.RESTRICTED, restrictions=["Contains alcohol content"])

        # Children's content product
        product = {"product_id": "prod_1", "name": "Kids Section Display", "targeted_ages": "children"}

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)
        assert not eligible
        assert "alcohol advertising cannot run on child-focused content" in reason

    def test_age_verification_allows_restricted_content(self, policy_service):
        """Test that age-verified products can show restricted content."""
        policy_result = PolicyCheckResult(status=PolicyStatus.RESTRICTED, restrictions=["Contains alcohol advertising"])

        # Adult product with age verification
        product = {
            "product_id": "prod_1",
            "name": "Adult Section Display",
            "targeted_ages": "adults",
            "verified_minimum_age": 21,
        }

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)
        assert eligible
        assert reason is None

    def test_allowed_brief_eligible_product(self, policy_service):
        """Test that allowed briefs can use products."""
        policy_result = PolicyCheckResult(status=PolicyStatus.ALLOWED)

        product = {"product_id": "prod_1", "name": "Standard Display", "targeted_ages": "adults"}

        eligible, reason = policy_service.check_product_eligibility(policy_result, product)
        assert eligible
        assert reason is None


class TestIntegration:
    """Test integration with get_products endpoint."""

    @pytest.mark.asyncio
    async def test_promoted_offering_included(self, policy_service):
        """Test that promoted_offering is included in analysis."""
        result = await policy_service.check_brief_compliance(
            brief="Advertisement for wellness products",
            promoted_offering="Weight loss pills - Lose 30 pounds in 30 days guaranteed!",
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
        promoted_offering="TechCorp - Latest 5G smartphone with advanced features",
    )

    # Verify the request has the new field
    assert hasattr(request, "promoted_offering")
    assert request.promoted_offering == "TechCorp - Latest 5G smartphone with advanced features"
