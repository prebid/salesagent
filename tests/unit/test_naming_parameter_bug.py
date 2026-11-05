"""Test for naming parameter handling.

Documents the correct usage of build_order_name_context() with template,
tenant_ai_config and media_buy_id. Everything after end_time is keyword-only.

Related fixes:
- adapters pass the tenant's TenantContext.ai_config as tenant_ai_config=value.
- media_buy_id is now included in the context dict for template substitution.
- the model is asked for {auto_name} only when the template names it.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.core.utils.naming import apply_naming_template, build_order_name_context
from src.services.ai.config import DEFAULT_GOOGLE_MODEL, TenantAIConfig
from tests.helpers.settings_injection import PlatformAICredential


@pytest.fixture(autouse=True)
def _no_platform_ai_key():
    """Keep these tests offline.

    tests/conftest.py sets GEMINI_API_KEY for every test, and the factory builds a model
    on the platform key alone, so any build_order_name_context() call here reached
    the real naming agent and opened a socket to generativelanguage.googleapis.com.
    None of these tests is about the agent - they grade parameter handling and template
    substitution - so the platform credential is removed and generate_auto_name takes
    its documented fallback branch.
    """
    with PlatformAICredential():
        yield


class TestBuildOrderNameContextParameters:
    """Tests for build_order_name_context parameter handling."""

    def test_positional_ai_config_is_refused(self):
        """Everything after end_time is keyword-only, so a key string passed positionally raises.

        The live example was ``build_order_name_context(..., gemini_key)``: an API-key
        string landing in the AI-configuration parameter. That used to be caught deeper
        down, by TenantAIConfig.coerce; the signature now refuses it before the call runs.
        """
        request = MagicMock()
        request.brand = MagicMock(domain="testbrand.com")
        request.packages = []
        gemini_key = "AIzaSy..."  # A string API key

        with pytest.raises(TypeError, match="positional argument"):
            build_order_name_context(request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), gemini_key)  # type: ignore[misc]

    def test_keyword_arg_works_correctly(self):
        """The seller's AI configuration, passed by keyword, reaches generate_auto_name."""
        request = MagicMock()
        request.brand = MagicMock(domain="testbrand.com")
        request.get_total_budget.return_value = 1000
        request.packages = []

        packages = []
        start_time = datetime(2025, 1, 1)
        end_time = datetime(2025, 1, 31)
        config = TenantAIConfig(provider="google", model=DEFAULT_GOOGLE_MODEL, api_key="AIzaSy...")

        # Patched so the assertion below can grade WHERE the configuration landed without
        # the naming agent making a real call to the provider on a fake key - this test
        # used to open a live socket to generativelanguage.googleapis.com.
        with patch("src.core.utils.naming.generate_auto_name") as auto_name:
            auto_name.return_value = "Testbrand Campaign"
            # Correct usage - using keyword argument
            context = build_order_name_context(
                request,
                packages,
                start_time,
                end_time,
                template="{auto_name}",
                tenant_ai_config=config,  # Correct!
            )

        assert context["auto_name"] == "Testbrand Campaign"
        assert "date_range" in context
        auto_name.assert_called_once_with(
            request=request,
            packages=packages,
            start_time=start_time,
            end_time=end_time,
            tenant_ai_config=config,
        )


class TestAutoNameIsAskedOnlyWhenTheTemplateNamesIt:
    """The model runs on the create_media_buy write path, so it is asked only for a name the template uses."""

    @pytest.mark.parametrize(
        ("template", "asked"),
        [
            ("{campaign_name|brand_name} - {media_buy_id} - {date_range}", False),
            ("{brand_name} {auto}", False),
            ("{auto_name}", True),
            ("{auto_name|brand_name} - {date_range}", True),
            ("{campaign_name| auto_name }", True),
        ],
        ids=["default-template", "similar-name", "alone", "first-alternative", "later-alternative-spaced"],
    )
    def test_generate_auto_name_runs_exactly_when_referenced(self, template, asked):
        request = MagicMock()
        request.brand = MagicMock(domain="acme.com")
        request.packages = []
        config = TenantAIConfig(provider="google", model=DEFAULT_GOOGLE_MODEL, api_key="seller-key")

        with patch("src.core.utils.naming.generate_auto_name", return_value="AI Name") as auto_name:
            context = build_order_name_context(
                request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), template=template, tenant_ai_config=config
            )

        assert auto_name.call_count == (1 if asked else 0)
        assert context["auto_name"] == ("AI Name" if asked else None)


_TEMPLATE = "{campaign_name|brand_name} - {media_buy_id} - {date_range}"


class TestMediaBuyIdInContext:
    """Tests for media_buy_id in build_order_name_context."""

    @staticmethod
    def _make_request():
        request = MagicMock()
        request.brand = MagicMock(domain="acme.com")
        request.get_total_budget.return_value = 1000
        request.packages = []
        return request

    def test_media_buy_id_present_in_context(self):
        """media_buy_id should be in the context when provided."""
        request = self._make_request()
        context = build_order_name_context(
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), template=_TEMPLATE, media_buy_id="buy_abc123"
        )
        assert context["media_buy_id"] == "buy_abc123"

    def test_media_buy_id_empty_when_not_provided(self):
        """media_buy_id should default to empty string when not provided."""
        request = self._make_request()
        context = build_order_name_context(request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), template=_TEMPLATE)
        assert context["media_buy_id"] == ""

    def test_template_renders_media_buy_id(self):
        """Template with {media_buy_id} should render correctly."""
        request = self._make_request()
        context = build_order_name_context(
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), template=_TEMPLATE, media_buy_id="buy_abc123"
        )
        result = apply_naming_template("{brand_name} - {media_buy_id} - {date_range}", context)
        assert "buy_abc123" in result
        assert "  " not in result

    def test_no_empty_placeholders_with_media_buy_id(self):
        """Template should not produce double-space artifacts when media_buy_id is provided."""
        request = self._make_request()
        context = build_order_name_context(
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), template=_TEMPLATE, media_buy_id="buy_abc123"
        )
        result = apply_naming_template("{campaign_name|brand_name} - {media_buy_id} - {date_range}", context)
        assert "  " not in result
        assert "buy_abc123" in result
