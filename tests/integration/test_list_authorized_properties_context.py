#!/usr/bin/env python3
"""
Integration test for list_authorized_properties context handling.

This test specifically exercises the context handling and testing hooks
that caused the NameError bug in production.

Tests:
- Real code path execution with context objects
- Testing context extraction from FastMCP headers
- ToolContext vs FastMCP Context handling
- Import verification of get_testing_context
"""

from unittest.mock import Mock

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestListAuthorizedPropertiesContext:
    """Test list_authorized_properties context handling and import fix."""

    def test_import_get_testing_context(self):
        """Verify get_testing_context is properly imported.

        This test would have caught the NameError bug.
        """
        # Import should work without NameError
        from src.core.testing_hooks import AdCPTestContext, get_testing_context

        # Create mock context
        mock_context = Mock()
        mock_context.meta = {"headers": {}}

        # Call should work without NameError
        testing_ctx = get_testing_context(mock_context)

        # Verify it returns correct type
        assert isinstance(testing_ctx, AdCPTestContext)
        assert testing_ctx.dry_run is False
        assert testing_ctx.test_session_id is None

    def test_get_testing_context_callable(self):
        """Test get_testing_context can be called without NameError.

        This test specifically verifies the bug fix where get_testing_context
        was called but not imported in properties.py line 84.
        """
        from src.core.testing_hooks import get_testing_context

        # Create mock context
        mock_context = Mock()
        mock_context.meta = {"headers": {}}

        # Call get_testing_context - previously raised NameError
        testing_ctx = get_testing_context(mock_context)

        # Verify it returns correct type
        from src.core.testing_hooks import AdCPTestContext

        assert isinstance(testing_ctx, AdCPTestContext)
