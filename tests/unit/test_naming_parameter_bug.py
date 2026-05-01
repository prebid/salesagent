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
from unittest.mock import MagicMock

import pytest

from src.core.utils.naming import apply_naming_template, build_order_name_context


class TestBuildOrderNameContextParameters:
    """Tests for build_order_name_context parameter handling."""

    def test_positional_string_arg_causes_error(self):
        """Passing a string as 5th positional arg causes AttributeError.

        The function signature is:
            build_order_name_context(request, packages, start, end, tenant_ai_config=None, tenant_gemini_key=None)

        Passing a string as 5th positional arg makes it tenant_ai_config,
        which then fails when .api_key is accessed.

        This test documents why callers MUST use keyword arguments.
        """
        request = MagicMock()
        request.buyer_ref = "TEST-001"
        request.brand = MagicMock(domain="testbrand.com")
        request.get_total_budget.return_value = 1000
        request.packages = []

        packages = []
        start_time = datetime(2025, 1, 1)
        end_time = datetime(2025, 1, 31)
        gemini_key = "AIzaSy..."  # A string API key

        # This fails because the string is treated as tenant_ai_config
        with pytest.raises(AttributeError, match="api_key"):
            build_order_name_context(request, packages, start_time, end_time, gemini_key)  # Wrong!

    def test_keyword_arg_works_correctly(self):
        """Passing gemini_key as keyword argument works correctly."""
        request = MagicMock()
        request.buyer_ref = "TEST-001"
        request.brand = MagicMock(domain="testbrand.com")
        request.get_total_budget.return_value = 1000
        request.packages = []

        packages = []
        start_time = datetime(2025, 1, 1)
        end_time = datetime(2025, 1, 31)
        gemini_key = "AIzaSy..."

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

    def test_buyer_ref_alias_maps_to_media_buy_id(self):
        """buyer_ref should be a backward-compatible alias for media_buy_id."""
        request = self._make_request()
        context = build_order_name_context(
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), media_buy_id="buy_xyz789"
        )
        assert context["buyer_ref"] == "buy_xyz789"
        assert context["buyer_ref"] == context["media_buy_id"]

    def test_template_renders_media_buy_id(self):
        """Template with {media_buy_id} should render correctly."""
        request = self._make_request()
        context = build_order_name_context(
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), media_buy_id="buy_abc123"
        )
        result = apply_naming_template("{brand_name} - {media_buy_id} - {date_range}", context)
        assert "buy_abc123" in result
        assert "  " not in result

    def test_legacy_buyer_ref_template_renders_media_buy_id(self):
        """Legacy template with {buyer_ref} should render media_buy_id value."""
        request = self._make_request()
        context = build_order_name_context(
            request, [], datetime(2025, 1, 1), datetime(2025, 1, 31), media_buy_id="buy_abc123"
        )
        result = apply_naming_template("{brand_name} - {buyer_ref} - {date_range}", context)
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
