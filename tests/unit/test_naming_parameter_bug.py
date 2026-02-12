"""Test for naming parameter handling.

Documents the correct usage of build_order_name_context() with tenant_gemini_key.
The function signature has tenant_ai_config as the 5th parameter and
tenant_gemini_key as the 6th, so callers must use keyword arguments.

Related fix: adapters now use tenant_gemini_key=value instead of positional arg.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.core.utils.naming import build_order_name_context


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
        request.brand_manifest = MagicMock(name="Test Brand")
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
        request.brand_manifest = MagicMock(name="Test Brand")
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
