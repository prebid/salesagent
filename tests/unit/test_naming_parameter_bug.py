"""Test for naming parameter handling.

Documents the correct usage of render_order_name() with template, tenant_ai_config
and media_buy_id. Everything after end_time is keyword-only.

Related fixes:
- adapters pass the tenant's TenantContext.ai_config as tenant_ai_config=value.
- media_buy_id is now included in the context dict for template substitution.
- the model is asked for {auto_name} only when the template names it.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.core.utils.naming import DEFAULT_ORDER_NAME_TEMPLATE, render_order_name
from src.services.ai.config import DEFAULT_GOOGLE_MODEL, TenantAIConfig
from tests.helpers.settings_injection import PlatformAICredential


@pytest.fixture(autouse=True)
def _no_platform_ai_key():
    """Keep these tests offline.

    tests/conftest.py sets GEMINI_API_KEY for every test, and the factory builds a model
    on the platform key alone, so any render_order_name() call naming {auto_name} reached
    the real naming agent and opened a socket to generativelanguage.googleapis.com.
    None of these tests is about the agent - they grade parameter handling and template
    substitution - so the platform credential is removed and generate_auto_name takes
    its documented fallback branch.
    """
    with PlatformAICredential():
        yield


def _make_request(domain: str = "acme.com") -> MagicMock:
    """The adapter's create request as the naming path reads it: a brand domain and no packages."""
    request = MagicMock()
    request.brand = MagicMock(domain=domain)
    request.packages = []
    return request


class TestRenderOrderNameParameters:
    """Tests for render_order_name parameter handling."""

    def test_positional_ai_config_is_refused(self):
        """Everything after end_time is keyword-only, so a key string passed positionally raises.

        The live example was ``build_order_name_context(..., gemini_key)``, the context
        builder render_order_name replaced: an API-key
        string landing in the AI-configuration parameter. The signature refuses it before
        the call runs.
        """
        request = _make_request(domain="testbrand.com")
        gemini_key = "AIzaSy..."  # A string API key

        with pytest.raises(TypeError, match="positional argument"):
            render_order_name(None, request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), gemini_key)  # type: ignore[misc]

    def test_keyword_arg_works_correctly(self):
        """The seller's AI configuration, passed by keyword, reaches generate_auto_name."""
        request = _make_request(domain="testbrand.com")

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
            name = render_order_name(
                "{auto_name} ({date_range})",
                request,
                packages,
                start_time,
                end_time,
                tenant_ai_config=config,  # Correct!
            )

        assert name == "Testbrand Campaign (Jan 01-31, 2025)"
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
        ("template", "asked", "rendered"),
        [
            ("{campaign_name|brand_name} - {media_buy_id} - {date_range}", False, "acme.com -  - Jan 01-31, 2025"),
            ("{brand_name} {auto}", False, "acme.com "),
            ("{auto_name}", True, "AI Name"),
            ("{auto_name|brand_name} - {date_range}", True, "AI Name - Jan 01-31, 2025"),
            ("{campaign_name| auto_name }", True, "acme.com"),
        ],
        ids=["default-template", "similar-name", "alone", "first-alternative", "later-alternative-spaced"],
    )
    def test_generate_auto_name_runs_exactly_when_referenced(self, template, asked, rendered):
        request = _make_request()
        config = TenantAIConfig(provider="google", model=DEFAULT_GOOGLE_MODEL, api_key="seller-key")

        with patch("src.core.utils.naming.generate_auto_name", return_value="AI Name") as auto_name:
            name = render_order_name(
                template, request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), tenant_ai_config=config
            )

        assert auto_name.call_count == (1 if asked else 0)
        assert name == rendered

    def test_no_template_renders_the_default_without_asking_the_model(self):
        """None is the seller who configured no template: the default, which names no auto_name."""
        request = _make_request()

        with patch("src.core.utils.naming.generate_auto_name", return_value="AI Name") as auto_name:
            name = render_order_name(
                None, request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), media_buy_id="buy_abc123"
            )

        assert name == "acme.com - buy_abc123 - Jan 01-31, 2025"
        assert auto_name.call_count == 0
        assert "auto_name" not in DEFAULT_ORDER_NAME_TEMPLATE


class TestMediaBuyIdInOrderName:
    """Tests for media_buy_id in render_order_name."""

    def test_media_buy_id_is_rendered(self):
        """media_buy_id should be substituted when provided."""
        name = render_order_name(
            "{media_buy_id}",
            _make_request(),
            [],
            datetime(2025, 1, 1),
            datetime(2025, 1, 31),
            media_buy_id="buy_abc123",
        )
        assert name == "buy_abc123"

    def test_media_buy_id_empty_when_not_provided(self):
        """media_buy_id should default to empty string when not provided."""
        name = render_order_name("{media_buy_id}", _make_request(), [], datetime(2025, 1, 1), datetime(2025, 1, 31))
        assert name == ""

    @pytest.mark.parametrize(
        "template",
        ["{brand_name} - {media_buy_id} - {date_range}", "{campaign_name|brand_name} - {media_buy_id} - {date_range}"],
    )
    def test_no_empty_placeholders_with_media_buy_id(self, template):
        """Template should not produce double-space artifacts when media_buy_id is provided."""
        name = render_order_name(
            template, _make_request(), [], datetime(2025, 1, 1), datetime(2025, 1, 31), media_buy_id="buy_abc123"
        )
        assert "  " not in name
        assert "buy_abc123" in name
