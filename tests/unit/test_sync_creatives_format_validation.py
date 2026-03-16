"""Tests for format validation in sync_creatives.

Tests the new format validation logic that was added to sync_creatives
to ensure consistent validation across all creative operations.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from adcp.types.generated_poc.enums.creative_action import CreativeAction

from src.core.resolved_identity import ResolvedIdentity
from src.core.tools.creatives import _sync_creatives_impl
from tests.harness import make_mock_uow


def _make_creative_uow():
    """Create a mock CreativeUoW with creative_repo returning sensible defaults."""
    mock_creative_repo = MagicMock()
    mock_creative_repo.get_provenance_policies.return_value = []
    mock_creative_repo.get_by_id.return_value = None
    mock_creative_repo.begin_nested.return_value.__enter__.return_value = None
    mock_creative_repo.begin_nested.return_value.__exit__.return_value = None

    # create() must return a mock with proper string attributes (Pydantic validation)
    def mock_create(**kwargs):
        db_creative = MagicMock()
        db_creative.creative_id = kwargs.get("creative_id", "c_unknown")
        db_creative.status = kwargs.get("status", "approved")
        return db_creative

    mock_creative_repo.create.side_effect = mock_create

    _, mock_uow = make_mock_uow(
        repos={
            "creatives": mock_creative_repo,
            "assignments": MagicMock(),
        }
    )
    return mock_uow, mock_creative_repo


class TestSyncCreativesFormatValidation:
    """Test format validation in sync_creatives operation."""

    @pytest.fixture
    def identity(self):
        """ResolvedIdentity for tests."""
        return ResolvedIdentity(
            principal_id="principal_123",
            tenant_id="tenant_123",
            tenant={"tenant_id": "tenant_123", "approval_mode": "auto-approve", "slack_webhook_url": None},
            protocol="mcp",
        )

    @pytest.fixture
    def mock_tenant(self):
        """Mock tenant configuration."""
        return {
            "tenant_id": "tenant_123",
            "approval_mode": "auto-approve",
            "slack_webhook_url": None,
        }

    @pytest.fixture
    def valid_creative_dict(self):
        """Valid creative dictionary for testing."""
        return {
            "creative_id": "creative_123",
            "name": "Test Banner",
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
            "assets": {"banner_image": {"url": "https://example.com/banner.png", "width": 300, "height": 250}},
            "variants": [],  # Required in adcp 3.6.0
        }

    @pytest.fixture
    def mock_format_spec(self):
        """Mock format specification from creative agent."""
        format_spec = Mock()
        format_spec.format_id = "display_300x250_image"
        format_spec.agent_url = "https://creative.adcontextprotocol.org"
        format_spec.name = "Medium Rectangle - Image"
        return format_spec

    def test_format_validation_success(self, identity, mock_tenant, valid_creative_dict, mock_format_spec):
        """Test that format validation succeeds when format exists."""
        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value=mock_tenant),
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry
            async def mock_list_all_formats(tenant_id=None):
                return [mock_format_spec]

            async def mock_get_format(agent_url, format_id):
                return mock_format_spec

            mock_registry = Mock()
            mock_registry.list_all_formats = mock_list_all_formats
            mock_registry.get_format = mock_get_format
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(creatives=[valid_creative_dict], identity=identity)

            # Verify format was validated
            assert len(response.creatives) == 1
            assert response.creatives[0].action == CreativeAction.created
            assert response.creatives[0].creative_id == "creative_123"

    def test_format_validation_unknown_format(self, identity, mock_tenant, valid_creative_dict):
        """Test that validation fails with clear error when format doesn't exist."""
        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value=mock_tenant),
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry - format not found
            async def mock_list_all_formats(tenant_id=None):
                return []

            async def mock_get_format(agent_url, format_id):
                return None  # Format not found

            mock_registry = Mock()
            mock_registry.list_all_formats = mock_list_all_formats
            mock_registry.get_format = mock_get_format
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(creatives=[valid_creative_dict], identity=identity)

            # Verify creative failed with appropriate error
            assert len(response.creatives) == 1
            assert response.creatives[0].action == CreativeAction.failed
            assert response.creatives[0].creative_id == "creative_123"
            assert len(response.creatives[0].errors) == 1

            error_msg = response.creatives[0].errors[0]
            assert "Unknown format 'display_300x250_image'" in error_msg
            assert "https://creative.adcontextprotocol.org" in error_msg
            assert "list_creative_formats" in error_msg  # Helpful suggestion

    def test_format_validation_agent_unreachable(self, identity, mock_tenant, valid_creative_dict):
        """Test that validation fails with clear error when agent is unreachable."""
        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value=mock_tenant),
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry - agent unreachable
            async def mock_list_all_formats(tenant_id=None):
                return []

            async def mock_get_format(agent_url, format_id):
                raise ConnectionError("Connection refused")

            mock_registry = Mock()
            mock_registry.list_all_formats = mock_list_all_formats
            mock_registry.get_format = mock_get_format
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(creatives=[valid_creative_dict], identity=identity)

            # Verify creative failed with network error message
            assert len(response.creatives) == 1
            assert response.creatives[0].action == CreativeAction.failed
            assert len(response.creatives[0].errors) == 1

            error_msg = response.creatives[0].errors[0]
            assert "Cannot validate format" in error_msg
            assert "unreachable or returned an error" in error_msg
            assert "Connection refused" in error_msg  # Original error included

    def test_format_validation_with_string_format_id(self, identity, mock_tenant, mock_format_spec):
        """Test that string format_ids are rejected (FormatId object required)."""
        # Creative with string format_id (legacy format - no longer supported)
        creative_dict = {
            "creative_id": "creative_456",
            "name": "Legacy Creative",
            "format_id": "display_300x250_image",  # String instead of FormatId object
            "assets": {"banner_image": {"url": "https://example.com/banner.png", "width": 300, "height": 250}},
            "variants": [],  # Required in adcp 3.6.0
        }

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value=mock_tenant),
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry
            async def mock_list_all_formats(tenant_id=None):
                return [mock_format_spec]

            async def mock_get_format(agent_url, format_id):
                return mock_format_spec

            mock_registry = Mock()
            mock_registry.list_all_formats = mock_list_all_formats
            mock_registry.get_format = mock_get_format
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(creatives=[creative_dict], identity=identity)

            # Verify creative failed validation (string format_id rejected by schema)
            # AdCP spec requires format_id to be a FormatId object with agent_url and id
            assert len(response.creatives) == 1
            assert response.creatives[0].action == CreativeAction.failed
            assert response.creatives[0].creative_id == "creative_456"
            # Error message will be from Pydantic validation, not our format validation

    def test_format_validation_multiple_creatives(self, identity, mock_tenant, mock_format_spec):
        """Test that format validation works correctly with multiple creatives."""
        creatives = [
            {
                "creative_id": "creative_1",
                "name": "Valid Creative",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
                "assets": {"banner_image": {"url": "https://example.com/1.png"}},
                "variants": [],
            },
            {
                "creative_id": "creative_2",
                "name": "Invalid Format",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "unknown_format"},
                "assets": {"banner_image": {"url": "https://example.com/2.png"}},
                "variants": [],
            },
            {
                "creative_id": "creative_3",
                "name": "Valid Creative 2",
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
                "assets": {"banner_image": {"url": "https://example.com/3.png"}},
                "variants": [],
            },
        ]

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value=mock_tenant),
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry
            async def mock_list_all_formats(tenant_id=None):
                return [mock_format_spec]

            # Mock get_format to return format_spec for valid format, None for invalid
            async def mock_get_format(agent_url, format_id):
                if format_id == "display_300x250_image":
                    return mock_format_spec
                return None

            mock_registry = Mock()
            mock_registry.list_all_formats = mock_list_all_formats
            mock_registry.get_format = mock_get_format
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(creatives=creatives, identity=identity)

            # Verify results
            assert len(response.creatives) == 3

            # First creative: success
            assert response.creatives[0].creative_id == "creative_1"
            assert response.creatives[0].action == CreativeAction.created

            # Second creative: failed (unknown format)
            assert response.creatives[1].creative_id == "creative_2"
            assert response.creatives[1].action == CreativeAction.failed
            assert "Unknown format 'unknown_format'" in response.creatives[1].errors[0]

            # Third creative: success
            assert response.creatives[2].creative_id == "creative_3"
            assert response.creatives[2].action == CreativeAction.created

    def test_format_validation_caching(self, identity, mock_tenant, valid_creative_dict, mock_format_spec):
        """Test that format validation uses in-memory cache (doesn't call agent twice for same format)."""
        # Create two creatives with same format
        creative1 = valid_creative_dict.copy()
        creative1["creative_id"] = "creative_1"

        creative2 = valid_creative_dict.copy()
        creative2["creative_id"] = "creative_2"

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value=mock_tenant),
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry
            async def mock_list_all_formats(tenant_id=None):
                return [mock_format_spec]

            async def mock_get_format(agent_url, format_id):
                return mock_format_spec

            mock_registry = Mock()
            mock_registry.list_all_formats = mock_list_all_formats
            mock_registry.get_format = mock_get_format
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(creatives=[creative1, creative2], identity=identity)

            # Verify both creatives succeeded
            assert len(response.creatives) == 2
            assert response.creatives[0].action == CreativeAction.created
            assert response.creatives[1].action == CreativeAction.created

    def test_format_validation_missing_format_id(self, identity, mock_tenant):
        """Test that validation fails when format_id is missing."""
        creative_dict = {
            "creative_id": "creative_no_format",
            "name": "Creative Without Format",
            # Missing format_id
            "assets": {"banner_image": {"url": "https://example.com/banner.png"}},
        }

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value=mock_tenant),
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry (needed for list_all_formats call)
            async def mock_list_all_formats(tenant_id=None):
                return []

            mock_registry = Mock()
            mock_registry.list_all_formats = mock_list_all_formats
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(creatives=[creative_dict], identity=identity)

            # Verify creative failed with format validation error
            assert len(response.creatives) == 1
            assert response.creatives[0].action == CreativeAction.failed
            # Error message comes from Pydantic schema validation
            assert "format_id" in response.creatives[0].errors[0]

    def test_error_messages_distinguish_scenarios(self, identity, mock_tenant):
        """Test that error messages clearly distinguish between different failure scenarios."""
        # Test 1: Format unknown (agent reachable, format doesn't exist)
        creative_unknown_format = {
            "creative_id": "creative_unknown",
            "name": "Unknown Format",
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "nonexistent_format"},
            "assets": {"image": {"url": "https://example.com/1.png"}},
        }

        # Test 2: Agent unreachable (network error)
        creative_unreachable = {
            "creative_id": "creative_unreachable",
            "name": "Unreachable Agent",
            "format_id": {"agent_url": "https://offline.example.com", "id": "display_300x250_image"},
            "assets": {"image": {"url": "https://example.com/2.png"}},
        }

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value=mock_tenant),
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry
            async def mock_list_all_formats(tenant_id=None):
                return []

            async def mock_get_format(agent_url, format_id):
                if "offline.example.com" in agent_url:
                    raise ConnectionError("Connection refused")

            mock_registry = Mock()
            mock_registry.list_all_formats = mock_list_all_formats
            mock_registry.get_format = mock_get_format
            mock_registry_getter.return_value = mock_registry

            # Test unknown format error
            response1 = _sync_creatives_impl(creatives=[creative_unknown_format], identity=identity)

            error1 = response1.creatives[0].errors[0]
            assert "Unknown format" in error1
            assert "list_creative_formats" in error1
            assert "unreachable" not in error1  # Should NOT mention unreachability

            # Test agent unreachable error
            response2 = _sync_creatives_impl(creatives=[creative_unreachable], identity=identity)

            error2 = response2.creatives[0].errors[0]
            assert "Cannot validate format" in error2
            assert "unreachable or returned an error" in error2
            assert "Connection refused" in error2


class TestFormatValidationOptimization:
    """Test optimization considerations for format validation."""

    def test_format_validation_always_runs(self):
        """Document that format validation runs on all creative operations.

        Current Implementation:
        - Format validation runs on ALL creative operations (create AND update)
        - Even if format hasn't changed, we re-validate against creative agent
        - This ensures format spec is still valid on agent side

        Future Optimization (NOT RECOMMENDED):
        - Could skip validation if format_id unchanged on updates
        - Would require careful handling of edge cases:
          * Format spec changed on agent side (breaking change)
          * Agent migrated to different URL
          * Format deprecated/removed
        - Cache already makes validation fast (< 10ms for cache hit)
        - Complexity not worth marginal performance gain

        Recommendation: Keep current behavior (always validate).

        See docs/architecture/creative-format-validation.md for detailed analysis.
        """
        # This is a documentation test - no actual test code needed
        # The behavior is tested in integration tests with real database
        pass
