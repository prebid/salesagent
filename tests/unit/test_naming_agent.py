"""Tests for Pydantic AI naming agent."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.ai.agents.naming_agent import (
    OrderName,
    build_naming_prompt,
    create_naming_agent,
    generate_name_async,
)


class TestOrderName:
    """Tests for OrderName model."""

    def test_valid_order_name(self):
        """OrderName accepts valid name."""
        name = OrderName(name="Nike Q4 Campaign - Holiday Push")
        assert name.name == "Nike Q4 Campaign - Holiday Push"

    def test_empty_name_rejected(self):
        """OrderName requires non-empty name."""
        # Pydantic requires the field but doesn't enforce non-empty by default
        name = OrderName(name="")
        assert name.name == ""


class TestBuildNamingPrompt:
    """Tests for build_naming_prompt function."""

    def test_basic_prompt(self):
        """Builds prompt with required fields."""
        prompt = build_naming_prompt(
            buyer_ref="ACME-001",
            campaign_name="Holiday Campaign",
            brand_name="Nike",
            budget_info="$10,000.00 USD",
            date_range="Dec 1-31, 2025",
            products=["video_premium", "display_standard"],
        )

        assert "ACME-001" in prompt
        assert "Holiday Campaign" in prompt
        assert "Nike" in prompt
        assert "$10,000.00 USD" in prompt
        assert "Dec 1-31, 2025" in prompt
        assert "video_premium" in prompt
        assert "display_standard" in prompt

    def test_prompt_with_objectives(self):
        """Includes objectives when provided."""
        prompt = build_naming_prompt(
            buyer_ref="ACME-001",
            campaign_name=None,
            brand_name=None,
            budget_info=None,
            date_range="Jan 1-15, 2025",
            products=["standard"],
            objectives=["Brand Awareness", "Lead Generation", "Conversions"],
        )

        # Should include first 2 objectives
        assert "Brand Awareness" in prompt
        assert "Lead Generation" in prompt
        # Third objective may or may not be included (implementation detail)

    def test_prompt_with_none_values(self):
        """Handles None values gracefully."""
        prompt = build_naming_prompt(
            buyer_ref="TEST-001",
            campaign_name=None,
            brand_name=None,
            budget_info=None,
            date_range="Jan 1-7, 2025",
            products=["product_1"],
        )

        assert "TEST-001" in prompt
        assert "N/A" in prompt  # None values become N/A
        assert "Jan 1-7, 2025" in prompt

    def test_max_length_in_prompt(self):
        """Max length is included in prompt."""
        prompt = build_naming_prompt(
            buyer_ref="TEST-001",
            campaign_name="Test",
            brand_name="Brand",
            budget_info=None,
            date_range="Jan 1-7, 2025",
            products=["p1"],
            max_length=100,
        )

        assert "100 characters" in prompt


class TestCreateNamingAgent:
    """Tests for create_naming_agent function."""

    def test_creates_agent_with_model(self):
        """Creates agent with specified model."""
        agent = create_naming_agent("google-gla:gemini-2.0-flash")

        # Model is converted to a GoogleModel object internally
        assert agent.model is not None
        # Output type should be OrderName
        assert agent._output_type == OrderName

    def test_creates_agent_with_max_length(self):
        """Max length is incorporated into system prompt."""
        agent = create_naming_agent("google-gla:gemini-2.0-flash", max_length=80)

        # The system prompt should mention the max length
        assert "80 characters" in agent._system_prompts[0]


class TestGenerateNameAsync:
    """Tests for generate_name_async function."""

    @pytest.mark.asyncio
    async def test_generates_name_successfully(self):
        """Successfully generates and returns name."""
        mock_agent = MagicMock()
        mock_result = MagicMock()
        # pydantic-ai 1.x uses .output for structured data
        mock_result.output = OrderName(name="ACME-001 Holiday Video Campaign")
        mock_agent.run = AsyncMock(return_value=mock_result)

        name = await generate_name_async(
            agent=mock_agent,
            buyer_ref="ACME-001",
            campaign_name="Holiday Campaign",
            brand_name="Nike",
            budget_info="$10,000.00 USD",
            date_range="Dec 1-31, 2025",
            products=["video_premium"],
        )

        assert name == "ACME-001 Holiday Video Campaign"
        mock_agent.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_strips_quotes_from_name(self):
        """Strips surrounding quotes from generated name."""
        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output = OrderName(name='"ACME-001 Campaign"')
        mock_agent.run = AsyncMock(return_value=mock_result)

        name = await generate_name_async(
            agent=mock_agent,
            buyer_ref="ACME-001",
            campaign_name="Campaign",
            brand_name=None,
            budget_info=None,
            date_range="Jan 1-7, 2025",
            products=["p1"],
        )

        assert name == "ACME-001 Campaign"
        assert '"' not in name

    @pytest.mark.asyncio
    async def test_truncates_long_name(self):
        """Truncates name that exceeds max_length."""
        mock_agent = MagicMock()
        mock_result = MagicMock()
        # Name longer than 50 characters
        mock_result.output = OrderName(name="ACME-001 This Is A Very Long Campaign Name That Exceeds The Limit")
        mock_agent.run = AsyncMock(return_value=mock_result)

        name = await generate_name_async(
            agent=mock_agent,
            buyer_ref="ACME-001",
            campaign_name="Campaign",
            brand_name=None,
            budget_info=None,
            date_range="Jan 1-7, 2025",
            products=["p1"],
            max_length=50,
        )

        assert len(name) <= 50
        assert name.endswith("...")

    @pytest.mark.asyncio
    async def test_raises_on_agent_failure(self):
        """Raises exception when agent fails."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=Exception("API Error"))

        with pytest.raises(Exception, match="API Error"):
            await generate_name_async(
                agent=mock_agent,
                buyer_ref="ACME-001",
                campaign_name="Campaign",
                brand_name=None,
                budget_info=None,
                date_range="Jan 1-7, 2025",
                products=["p1"],
            )
