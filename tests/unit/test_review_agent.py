"""Tests for Pydantic AI creative review agent."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.ai.agents.review_agent import (
    CreativeReviewResult,
    build_review_prompt,
    create_review_agent,
    parse_confidence_score,
    review_creative_async,
)


class TestCreativeReviewResult:
    """Tests for CreativeReviewResult model."""

    def test_valid_approve_result(self):
        """CreativeReviewResult accepts valid APPROVE decision."""
        result = CreativeReviewResult(
            decision="APPROVE",
            reason="Creative meets all criteria",
            confidence="high",
        )
        assert result.decision == "APPROVE"
        assert result.reason == "Creative meets all criteria"
        assert result.confidence == "high"

    def test_valid_reject_result(self):
        """CreativeReviewResult accepts valid REJECT decision."""
        result = CreativeReviewResult(
            decision="REJECT",
            reason="Missing required disclosure",
            confidence="high",
        )
        assert result.decision == "REJECT"
        assert result.confidence == "high"

    def test_valid_human_approval_result(self):
        """CreativeReviewResult accepts REQUIRE HUMAN APPROVAL decision."""
        result = CreativeReviewResult(
            decision="REQUIRE HUMAN APPROVAL",
            reason="Uncertain about brand safety",
            confidence="medium",
        )
        assert result.decision == "REQUIRE HUMAN APPROVAL"
        assert result.confidence == "medium"

    def test_default_confidence(self):
        """Confidence defaults to medium."""
        result = CreativeReviewResult(
            decision="APPROVE",
            reason="Looks good",
        )
        assert result.confidence == "medium"


class TestBuildReviewPrompt:
    """Tests for build_review_prompt function."""

    def test_basic_prompt(self):
        """Builds prompt with required fields."""
        prompt = build_review_prompt(
            review_criteria="Must include brand logo",
            creative_name="Summer Banner",
            creative_format="display",
            promoted_offering="Nike Shoes",
        )

        assert "Must include brand logo" in prompt
        assert "Summer Banner" in prompt
        assert "display" in prompt
        assert "Nike Shoes" in prompt

    def test_prompt_with_creative_data(self):
        """Includes creative data when provided."""
        prompt = build_review_prompt(
            review_criteria="Check for clickbait",
            creative_name="Video Ad",
            creative_format="video",
            promoted_offering="Product X",
            creative_data={"duration": 30, "tags": ["promo", "sale"]},
        )

        assert "duration" in prompt
        assert "30" in prompt
        assert "tags" in prompt

    def test_prompt_without_creative_data(self):
        """Handles None creative data."""
        prompt = build_review_prompt(
            review_criteria="Standard review",
            creative_name="Test Creative",
            creative_format="native",
            promoted_offering="Service Y",
            creative_data=None,
        )

        assert "Test Creative" in prompt
        assert "{}" in prompt  # Empty JSON


class TestCreateReviewAgent:
    """Tests for create_review_agent function."""

    def test_creates_agent_with_model(self):
        """Creates agent with specified model."""
        agent = create_review_agent("google-gla:gemini-2.0-flash")

        assert agent.model is not None
        assert agent._output_type == CreativeReviewResult


class TestParseConfidenceScore:
    """Tests for parse_confidence_score function."""

    def test_high_confidence(self):
        """High confidence maps to 0.9."""
        assert parse_confidence_score("high") == 0.9
        assert parse_confidence_score("HIGH") == 0.9

    def test_medium_confidence(self):
        """Medium confidence maps to 0.6."""
        assert parse_confidence_score("medium") == 0.6
        assert parse_confidence_score("MEDIUM") == 0.6

    def test_low_confidence(self):
        """Low confidence maps to 0.3."""
        assert parse_confidence_score("low") == 0.3
        assert parse_confidence_score("LOW") == 0.3

    def test_unknown_defaults_to_medium(self):
        """Unknown confidence defaults to 0.6."""
        assert parse_confidence_score("unknown") == 0.6
        assert parse_confidence_score("very_high") == 0.6


class TestReviewCreativeAsync:
    """Tests for review_creative_async function."""

    @pytest.mark.asyncio
    async def test_returns_approval(self):
        """Successfully returns approval result."""
        mock_agent = MagicMock()
        mock_result = MagicMock()
        # pydantic-ai 1.x uses .output for structured data
        mock_result.output = CreativeReviewResult(
            decision="APPROVE",
            reason="Creative meets all criteria",
            confidence="high",
        )
        mock_agent.run = AsyncMock(return_value=mock_result)

        result = await review_creative_async(
            agent=mock_agent,
            review_criteria="Standard criteria",
            creative_name="Test Banner",
            creative_format="display",
            promoted_offering="Test Product",
        )

        assert result.decision == "APPROVE"
        assert result.confidence == "high"
        mock_agent.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_rejection(self):
        """Successfully returns rejection result."""
        mock_agent = MagicMock()
        mock_result = MagicMock()
        # pydantic-ai 1.x uses .output for structured data
        mock_result.output = CreativeReviewResult(
            decision="REJECT",
            reason="Missing required disclosure",
            confidence="high",
        )
        mock_agent.run = AsyncMock(return_value=mock_result)

        result = await review_creative_async(
            agent=mock_agent,
            review_criteria="Must have disclosure",
            creative_name="Promo Ad",
            creative_format="video",
            promoted_offering="Financial Service",
        )

        assert result.decision == "REJECT"
        assert "disclosure" in result.reason

    @pytest.mark.asyncio
    async def test_returns_human_approval(self):
        """Successfully returns human approval required result."""
        mock_agent = MagicMock()
        mock_result = MagicMock()
        # pydantic-ai 1.x uses .output for structured data
        mock_result.output = CreativeReviewResult(
            decision="REQUIRE HUMAN APPROVAL",
            reason="Cannot verify brand safety claims",
            confidence="low",
        )
        mock_agent.run = AsyncMock(return_value=mock_result)

        result = await review_creative_async(
            agent=mock_agent,
            review_criteria="Brand safety check",
            creative_name="Unclear Ad",
            creative_format="native",
            promoted_offering="Unknown Product",
        )

        assert result.decision == "REQUIRE HUMAN APPROVAL"
        assert result.confidence == "low"

    @pytest.mark.asyncio
    async def test_raises_on_agent_failure(self):
        """Raises exception when agent fails."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=Exception("API Error"))

        with pytest.raises(Exception, match="API Error"):
            await review_creative_async(
                agent=mock_agent,
                review_criteria="Criteria",
                creative_name="Test",
                creative_format="display",
                promoted_offering="Product",
            )
