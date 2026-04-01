"""Integration tests: internal behavior flags must not be controllable by external callers.

Security regression tests for salesagent-1dj and salesagent-su1.
Proves that include_performance, include_sub_assets, and include_snapshot
cannot be injected by buyers through request objects.

Uses the CreativeListEnv harness for real DB integration testing.
"""

import pytest
from pydantic import ValidationError

from tests.harness.creative_list import CreativeListEnv


@pytest.mark.requires_db
class TestListCreativesInternalFlagsIsolation:
    """Verify include_* flags cannot be injected via request object."""

    def test_request_object_rejects_include_performance(self, integration_db):
        """ListCreativesRequest schema must reject include_performance (not in AdCP spec)."""
        from src.core.schemas import ListCreativesRequest

        with pytest.raises(ValidationError, match="include_performance"):
            ListCreativesRequest(include_performance=True)

    def test_request_object_rejects_include_sub_assets(self, integration_db):
        """ListCreativesRequest schema must reject include_sub_assets (not in AdCP spec)."""
        from src.core.schemas import ListCreativesRequest

        with pytest.raises(ValidationError, match="include_sub_assets"):
            ListCreativesRequest(include_sub_assets=True)

    def test_request_object_accepts_include_assignments(self, integration_db):
        """include_assignments IS a valid AdCP 3.10 spec field — must be accepted."""
        from src.core.schemas import ListCreativesRequest

        req = ListCreativesRequest(include_assignments=True)
        assert req.include_assignments is True

    def test_impl_uses_explicit_parameters_not_request(self, integration_db):
        """_list_creatives_impl receives include_* as explicit params, not from request."""
        with CreativeListEnv() as env:
            env.setup_default_data()

            # Call impl with explicit flags — these come from the wrapper, not the buyer
            response = env.call_impl(include_performance=False, include_sub_assets=False)
            assert response is not None

    def test_mcp_call_succeeds_with_default_flags(self, integration_db):
        """MCP wrapper works with default include_* flags (harness simulates MCP transport)."""
        with CreativeListEnv() as env:
            env.setup_default_data()

            response = env.call_mcp()
            assert response is not None


@pytest.mark.requires_db
class TestGetMediaBuysInternalFlagsIsolation:
    """Verify include_snapshot cannot be injected via request object."""

    def test_request_object_rejects_include_snapshot(self, integration_db):
        """GetMediaBuysRequest schema must reject include_snapshot (not in AdCP spec)."""
        from src.core.schemas import GetMediaBuysRequest

        with pytest.raises(ValidationError, match="include_snapshot"):
            GetMediaBuysRequest(include_snapshot=True)
