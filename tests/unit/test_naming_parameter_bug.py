"""Test for naming parameter handling.

Documents the correct usage of build_order_name_context() with tenant_gemini_key
and media_buy_id. The function signature has tenant_ai_config as the 5th parameter,
tenant_gemini_key as the 6th, and media_buy_id as the 7th, so callers must use
keyword arguments.

Related fixes:
- adapters now use tenant_gemini_key=value instead of positional arg.
- media_buy_id is now included in the context dict for template substitution.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.core.utils.naming import apply_naming_template, build_order_name_context


@pytest.fixture(autouse=True)
def _no_platform_ai_key(monkeypatch):
    """Keep these tests offline.

    tests/conftest.py sets GEMINI_API_KEY for every test, and is_ai_enabled() returns
    True on the platform key alone, so any build_order_name_context() call here reached
    the real naming agent and opened a socket to generativelanguage.googleapis.com.
    None of these tests is about the agent - they grade parameter handling and template
    substitution - so the platform credential is removed and generate_auto_name takes
    its documented fallback branch.
    """
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "PYDANTIC_AI_PROVIDER", "PYDANTIC_AI_MODEL"):
        monkeypatch.delenv(var, raising=False)


class TestBuildOrderNameContextParameters:
    """Tests for build_order_name_context parameter handling."""

    def test_positional_string_arg_causes_error(self):
        """Passing a string as 5th positional arg raises.

        The function signature is:
            build_order_name_context(request, packages, start, end, tenant_ai_config=None, tenant_gemini_key=None)

        Passing a string as 5th positional arg makes it tenant_ai_config, which is a
        caller mistake and must be reported as one. This test documents why callers
        MUST use keyword arguments.

        RESTORED assertion. It had been rewritten to expect "one WARNING" instead,
        which pinned a caller bug as an acceptable degradation: the naming call then
        ran on somebody else's AI configuration and returned a plausible name. The
        exception TYPE moved — TenantAIConfig.coerce now raises TypeError at the parse
        boundary, naming the parameter and the offending type, instead of the old
        AttributeError thrown when the factory reached for `.api_key` several frames
        deeper — but "a caller mistake raises" is the contract, and it is back.
        """
        request = MagicMock()
        request.brand = MagicMock(domain="testbrand.com")
        request.get_total_budget.return_value = 1000
        request.packages = []

        packages = []
        start_time = datetime(2025, 1, 1)
        end_time = datetime(2025, 1, 31)
        gemini_key = "AIzaSy..."  # A string API key

        # This fails because the string is treated as tenant_ai_config
        with pytest.raises(TypeError) as excinfo:
            build_order_name_context(request, packages, start_time, end_time, gemini_key)  # Wrong!

        assert "str" in str(excinfo.value)
        assert "ai_config" in str(excinfo.value)

    def test_keyword_arg_works_correctly(self):
        """Passing gemini_key as keyword argument works correctly."""
        request = MagicMock()
        request.brand = MagicMock(domain="testbrand.com")
        request.get_total_budget.return_value = 1000
        request.packages = []

        packages = []
        start_time = datetime(2025, 1, 1)
        end_time = datetime(2025, 1, 31)
        gemini_key = "AIzaSy..."

        # Patched so the assertion below can grade WHERE the key landed without the
        # naming agent making a real call to the provider on a fake key - this test
        # used to open a live socket to generativelanguage.googleapis.com.
        with patch("src.core.utils.naming.generate_auto_name") as auto_name:
            auto_name.return_value = "Testbrand Campaign"
            # Correct usage - using keyword argument
            context = build_order_name_context(
                request,
                packages,
                start_time,
                end_time,
                tenant_gemini_key=gemini_key,  # Correct!
            )

        assert "brand_name" in context
        assert "date_range" in context
        # The point of the test: the key reaches the 6th parameter, not the 5th.
        assert auto_name.call_args.kwargs["tenant_gemini_key"] == gemini_key
        assert auto_name.call_args.kwargs["tenant_ai_config"] is None


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
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), media_buy_id="buy_abc123"
        )
        assert context["media_buy_id"] == "buy_abc123"

    def test_media_buy_id_empty_when_not_provided(self):
        """media_buy_id should default to empty string when not provided."""
        request = self._make_request()
        context = build_order_name_context(request, [], datetime(2025, 1, 1), datetime(2025, 1, 31))
        assert context["media_buy_id"] == ""

    def test_template_renders_media_buy_id(self):
        """Template with {media_buy_id} should render correctly."""
        request = self._make_request()
        context = build_order_name_context(
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), media_buy_id="buy_abc123"
        )
        result = apply_naming_template("{brand_name} - {media_buy_id} - {date_range}", context)
        assert "buy_abc123" in result
        assert "  " not in result

    def test_no_empty_placeholders_with_media_buy_id(self):
        """Template should not produce double-space artifacts when media_buy_id is provided."""
        request = self._make_request()
        context = build_order_name_context(
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), media_buy_id="buy_abc123"
        )
        result = apply_naming_template("{campaign_name|brand_name} - {media_buy_id} - {date_range}", context)
        assert "  " not in result
        assert "buy_abc123" in result
